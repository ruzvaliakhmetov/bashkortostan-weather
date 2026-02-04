import os
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime, timedelta

import requests
from PIL import Image, ImageDraw, ImageFont
from telegram import Bot, InputSticker
from telegram.error import BadRequest


IMAGES_DIR = Path(__file__).parent / "images"
ICONS_DIR = IMAGES_DIR / "icons"  # PNG-иконки погоды 225x225 (01d.png, 02n.png и т.п.)

# Погодные фоны (кладём в images/): bg_01d.png, bg_01n.png, ..., bg_50n.png
BG_PREFIX = "bg_"
BG_FALLBACK_1 = "bg_fallback.png"  # опционально (если есть)
BG_FALLBACK_2 = "bg_01d.png"       # запасной вариант, если нет bg_fallback.png


# ==========================
#   НАСТРОЙКИ МАКЕТА
# ==========================

@dataclass
class BlockLayout:
    x: int | None        # если right_align=False: x от левого края
                         # если right_align=True: отступ от правого края
    y: int | None        # y от верхнего края; если None — используем default_y или считаем от низа
    font_size: int
    right_align: bool = False  # правая выключка по x


@dataclass
class DetailsLayout:
    x: int
    y: int
    font_size: int
    line_spacing: int = 6


# ---- Лэйауты блоков (можно твикать под себя) ----

# Город
CITY_LAYOUT = BlockLayout(
    x=50,
    y=400,
    font_size=58,
)

# Температура (цифры) — ПРАВАЯ ВЫКЛЮЧКА
TEMP_LAYOUT = BlockLayout(
    x=80,          # правый край цифр будет в 80 px от правого края картинки
    y=30,
    font_size=140,
    right_align=True,
)

# Блок "°C" — отдельный независимый блок
DEGREE_LAYOUT = BlockLayout(
    x=430,
    y=56,
    font_size=42,
    right_align=False,
)

# День (например "07")
DAY_LAYOUT = BlockLayout(
    x=396,
    y=310,
    font_size=48,
)

# Месяц (например "Dec")
MONTH_LAYOUT = BlockLayout(
    x=396,
    y=280,
    font_size=30,
)

# Время (например "20:55")
TIME_LAYOUT = BlockLayout(
    x=400,
    y=366,
    font_size=20,
)

# Блок деталей (humidity, wind, conditions) — три строки
DETAILS_LAYOUT = DetailsLayout(
    x=50,
    y=290,
    font_size=30,
    line_spacing=6,
)

# Позиция иконки погоды (из локальных PNG 225x225)
ICON_X = 0
ICON_Y = 0
ICON_SIZE = (225, 225)


# ==========================
#   КОНФИГ ГОРОДОВ
# ==========================

@dataclass
class CityConfig:
    name: str          # как написать город на стикере
    query: str         # как отдать город в API
    emoji: str         # эмодзи для стикера
    output: str        # куда сохранить png
    tz_offset_hours: int = 0  # смещение от UTC, для локального времени города


CITIES = [
    CityConfig(name="Ufa",         query="Ufa,RU",          emoji="🏙️", output="sticker_ufa.png",         tz_offset_hours=5),
    CityConfig(name="Neftekamsk",  query="Neftekamsk,RU",   emoji="🏙️", output="sticker_neftekamsk.png",  tz_offset_hours=5),
    CityConfig(name="Dyurtyuli",   query="Dyurtyuli,RU",    emoji="🏙️", output="sticker_dyurtyuli.png",   tz_offset_hours=5),
    CityConfig(name="Mesyagutovo", query="Mesyagutovo,RU",  emoji="🏙️", output="sticker_mesyagutovo.png", tz_offset_hours=5),
    CityConfig(name="Kushnarenkovo", query="Kushnarënkovo, RU", emoji="🏙️", output="sticker_kushnarenkovo.png", tz_offset_hours=5),
    CityConfig(name="Tuymazy",     query="Tuymazy,RU",      emoji="🏙️", output="sticker_tuymazy.png",     tz_offset_hours=5),
    CityConfig(name="Sterlitamak", query="Sterlitamak,RU",  emoji="🏙️", output="sticker_sterlitamak.png", tz_offset_hours=5),
    CityConfig(name="Salavat",     query="Salavat,RU",      emoji="🏙️", output="sticker_salavat.png",     tz_offset_hours=5),
    CityConfig(name="Meleuz",      query="Meleuz,RU",       emoji="🏙️", output="sticker_meleuz.png",      tz_offset_hours=5),
    CityConfig(name="Kumertau",    query="Kumertau,RU",     emoji="🏙️", output="sticker_kumertau.png",    tz_offset_hours=5),
]


# ==========================
#   ШРИФТ
# ==========================

def get_font(size: int) -> ImageFont.FreeTypeFont:
    font_paths = [
        "font.ttf",
        "Font.ttf",
        "fonts/font.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in font_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


# ==========================
#   ПОГОДА
# ==========================

@dataclass
class WeatherInfo:
    temp: float
    humidity: int
    wind_speed: float
    description: str
    condition_main: str  # Clear, Clouds, Rain и т.п.
    icon_code: str       # код иконки, например "01d"


def fetch_weather(city: CityConfig) -> WeatherInfo:
    api_key = os.environ["WEATHER_API_KEY"]
    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city.query,
        "appid": api_key,
        "units": "metric",
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    main = data["main"]
    wind = data.get("wind", {})
    weather0 = data["weather"][0]

    return WeatherInfo(
        temp=main["temp"],
        humidity=main["humidity"],
        wind_speed=wind.get("speed", 0.0),
        description=weather0.get("description", "").capitalize(),
        condition_main=weather0.get("main", "Default"),
        icon_code=weather0.get("icon", ""),  # например: "01d"
    )


# ==========================
#   ФОН ПО ПОГОДЕ
# ==========================

def _get_background_path(icon_code: str) -> Path:
    """
    Возвращает путь к фону на основе icon_code (например "01d" -> images/bg_01d.png).
    Если не найден — пытается взять fallback.
    """
    if icon_code:
        candidate = IMAGES_DIR / f"{BG_PREFIX}{icon_code}.png"
        if candidate.exists():
            return candidate
        print(f"[warn] weather bg not found for icon '{icon_code}': {candidate}")

    # fallback #1: bg_fallback.png (если есть)
    fb1 = IMAGES_DIR / BG_FALLBACK_1
    if fb1.exists():
        return fb1

    # fallback #2: bg_01d.png (ожидаемо существует)
    fb2 = IMAGES_DIR / BG_FALLBACK_2
    if fb2.exists():
        return fb2

    raise FileNotFoundError(
        "No suitable background found. Expected one of:\n"
        f"- {IMAGES_DIR / f'{BG_PREFIX}{icon_code}.png'}\n"
        f"- {fb1}\n"
        f"- {fb2}\n"
        "Put weather backgrounds into the images/ folder."
    )


# ==========================
#   РИСОВАЛКИ
# ==========================

def _draw_text_block(
    draw: ImageDraw.ImageDraw,
    img: Image.Image,
    text: str,
    layout: BlockLayout,
    *,
    default_x: int | None = None,
    default_y: int | None = None,
    fill=(255, 255, 255, 255),
) -> tuple[int, int, int, int]:
    font = get_font(layout.font_size)
    tb = draw.textbbox((0, 0), text, font=font)
    text_w = tb[2] - tb[0]
    text_h = tb[3] - tb[1]

    # ---------- X ----------
    if layout.x is not None:
        if layout.right_align:
            x = img.width - layout.x - text_w
        else:
            x = layout.x
    elif default_x is not None:
        x = default_x
    else:
        x = (img.width - text_w) // 2

    # ---------- Y ----------
    if layout.y is not None:
        y = layout.y
    elif default_y is not None:
        y = default_y
    else:
        y = img.height - text_h - 40

    draw.text((x, y), text, font=font, fill=fill)
    return x, y, text_w, text_h


def _draw_details_block(draw: ImageDraw.ImageDraw, img: Image.Image, weather: WeatherInfo) -> None:
    font = get_font(DETAILS_LAYOUT.font_size)
    lines = [
        f"Humidity: {weather.humidity}%",
        f"Wind: {weather.wind_speed:.1f} m/s",
        weather.description,
    ]

    x = DETAILS_LAYOUT.x
    y = DETAILS_LAYOUT.y

    for line in lines:
        lb = draw.textbbox((0, 0), line, font=font)
        h = lb[3] - lb[1]
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += h + DETAILS_LAYOUT.line_spacing


def _paste_icon(img: Image.Image, icon_code: str) -> None:
    if not icon_code:
        return

    icon_path = ICONS_DIR / f"{icon_code}.png"
    if not icon_path.exists():
        print(f"[warn] icon not found: {icon_path}")
        return

    icon = Image.open(icon_path).convert("RGBA")
    if icon.size != ICON_SIZE:
        icon = icon.resize(ICON_SIZE, Image.LANCZOS)

    img.alpha_composite(icon, (ICON_X, ICON_Y))


# ==========================
#   ГЕНЕРАЦИЯ КАРТИНКИ
# ==========================

def generate_weather_image(
    city: CityConfig,
    weather: WeatherInfo,
    output_path: str,
    day_text: str,
    month_text: str,
    time_text: str,
) -> None:
    # --- фон по погоде (bg_01d.png, bg_02n.png, ...) ---
    bg_path = _get_background_path(weather.icon_code)
    img = Image.open(bg_path).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # --- иконка погоды ---
    _paste_icon(img, weather.icon_code)

    # --- ТЕМПЕРАТУРА (цифры) с правой выключкой ---
    temp_text = f"{round(weather.temp):d}"
    _draw_text_block(
        draw,
        img,
        temp_text,
        TEMP_LAYOUT,
        default_y=70,
    )

    # --- БЛОК "°C" ---
    _draw_text_block(
        draw,
        img,
        "°C",
        DEGREE_LAYOUT,
        default_y=70,
    )

    # --- Город ---
    _draw_text_block(draw, img, city.name, CITY_LAYOUT)

    # --- Дата / время обновления ---
    _draw_text_block(draw, img, day_text, DAY_LAYOUT)
    _draw_text_block(draw, img, month_text, MONTH_LAYOUT)
    _draw_text_block(draw, img, time_text, TIME_LAYOUT)

    # --- Детали (3 строки) ---
    _draw_details_block(draw, img, weather)

    img.save(output_path, format="PNG")


# ==========================
#   ОБНОВЛЕНИЕ СТИКЕРОВ
# ==========================

async def update_stickers() -> None:
    token = os.environ["BOT_TOKEN"]
    set_name = os.environ["STICKER_SET_NAME"]
    set_title = os.environ["STICKER_SET_TITLE"]
    owner_user_id = int(os.environ["TELEGRAM_USER_ID"])

    bot = Bot(token)

    new_stickers: list[InputSticker] = []

    for city in CITIES:
        weather = fetch_weather(city)

        utc_now = datetime.utcnow()
        city_now = utc_now + timedelta(hours=city.tz_offset_hours)

        day_text = city_now.strftime("%d")
        month_text = city_now.strftime("%b")
        time_text = city_now.strftime("%H:%M")

        generate_weather_image(city, weather, city.output, day_text, month_text, time_text)

        with open(city.output, "rb") as f:
            uploaded = await bot.upload_sticker_file(
                user_id=owner_user_id,
                sticker=f,
                sticker_format="static",
            )

        new_stickers.append(
            InputSticker(
                sticker=uploaded.file_id,
                emoji_list=[city.emoji],
                format="static",
            )
        )

    try:
        sticker_set = await bot.get_sticker_set(set_name)
    except BadRequest as e:
        msg = getattr(e, "message", str(e)).lower()
        print("get_sticker_set error:", msg)
        if "stickerset_invalid" in msg or "stickerset not found" in msg:
            await bot.create_new_sticker_set(
                user_id=owner_user_id,
                name=set_name,
                title=set_title,
                stickers=new_stickers,
                sticker_type="regular",
            )
            print(f"Created new sticker set {set_name} with weather stickers")
            return
        else:
            raise

    old_stickers = sticker_set.stickers
    old_count = len(old_stickers)
    new_count = len(new_stickers)
    common = min(old_count, new_count)

    for i in range(common):
        old_id = old_stickers[i].file_id
        new_st = new_stickers[i]
        try:
            await bot.replace_sticker_in_set(
                user_id=owner_user_id,
                name=set_name,
                old_sticker=old_id,
                sticker=new_st,
            )
            print(f"Replaced sticker {old_id} with new one at position {i}")
        except BadRequest as e:
            print("replace_sticker_in_set error:", getattr(e, "message", str(e)))

    if new_count > old_count:
        for i in range(common, new_count):
            st = new_stickers[i]
            try:
                await bot.add_sticker_to_set(
                    user_id=owner_user_id,
                    name=set_name,
                    sticker=st,
                )
                print(f"Added extra sticker at position {i}")
            except BadRequest as e:
                print("add_sticker_to_set error:", getattr(e, "message", str(e)))

    elif old_count > new_count:
        for i in range(common, old_count):
            old_id = old_stickers[i].file_id
            try:
                await bot.delete_sticker_from_set(old_id)
                print(f"Deleted extra old sticker {old_id} at position {i}")
            except BadRequest as e:
                print("delete_sticker_from_set error:", getattr(e, "message", str(e)))

    print(f"Updated sticker set {set_name} with weather for {new_count} cities")


if __name__ == "__main__":
    import asyncio
    asyncio.run(update_stickers())
