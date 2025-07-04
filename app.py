import os
import json
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
import matplotlib.pyplot as plt
from flask import Flask, render_template, request, redirect, url_for
import requests
from io import BytesIO
import base64

# Загрузка переменных окружения
load_dotenv()
API_KEY = os.getenv('OPENWEATHER_API_KEY')
CACHE_DIR = Path('cache')
CACHE_DIR.mkdir(exist_ok=True)

app = Flask(__name__)


# Пользовательские фильтры для Jinja2
@app.template_filter('strftime')
def format_datetime(value, format='%d.%m.%Y'):
    """Фильтр для форматирования даты"""
    if isinstance(value, str):
        try:
            # Преобразуем строку в дату
            dt = datetime.strptime(value, '%Y-%m-%d')
            return dt.strftime(format)
        except:
            return value
    elif hasattr(value, 'strftime'):
        return value.strftime(format)
    return value


@app.template_filter('rus_weekday')
def rus_weekday(date):
    """Перевод дня недели на русский"""
    days = {
        'Mon': 'Пн', 'Tue': 'Вт', 'Wed': 'Ср',
        'Thu': 'Чт', 'Fri': 'Пт', 'Sat': 'Сб', 'Sun': 'Вс'
    }
    if isinstance(date, str):
        try:
            date = datetime.strptime(date, '%Y-%m-%d')
        except:
            return date
    eng_day = date.strftime('%a')
    return days.get(eng_day, eng_day)


@app.template_filter('capitalize')
def capitalize_words(value):
    """Фильтр для капитализации строки"""
    return ' '.join(word.capitalize() for word in value.split())


@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        city = request.form.get('city', '').strip()
        if city:
            return redirect(url_for('weather', city=city))
    return render_template('index.html')


def get_weather_data(city: str) -> dict:
    """Получение данных о погоде с кэшированием"""
    cache_file = CACHE_DIR / f"{city.lower()}.json"

    # Проверка кэша (актуальность 10 минут)
    if cache_file.exists():
        cache_time = cache_file.stat().st_mtime
        if time.time() - cache_time < 600:
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)

    # Запрос к API
    url = f"http://api.openweathermap.org/data/2.5/forecast?q={city}&appid={API_KEY}&units=metric&lang=ru"
    response = requests.get(url)
    data = response.json()

    # Сохранение в кэш
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)

    return data


def process_weather_data(data: dict) -> tuple:
    """Обработка данных и расчет показателей"""
    dates, temps, descriptions = [], [], []
    weather_data = []

    for item in data['list']:
        dt = datetime.strptime(item['dt_txt'], '%Y-%m-%d %H:%M:%S')
        temp = item['main']['temp']
        desc = item['weather'][0]['description']
        icon = item['weather'][0]['icon']

        dates.append(dt)
        temps.append(temp)
        descriptions.append(desc)

        # Собираем данные для почасового прогноза
        weather_data.append({
            'datetime': dt,
            'temp': temp,
            'description': desc,
            'icon': f"http://openweathermap.org/img/wn/{icon}.png",
            'humidity': item['main']['humidity'],
            'wind': item['wind']['speed']
        })

    # Группировка по дням для ежедневного прогноза
    daily_data = {}
    for item in weather_data:
        date_key = item['datetime'].strftime('%Y-%m-%d')
        if date_key not in daily_data:
            daily_data[date_key] = {
                'min_temp': item['temp'],
                'max_temp': item['temp'],
                'temps': [],
                'icons': []
            }
        else:
            if item['temp'] < daily_data[date_key]['min_temp']:
                daily_data[date_key]['min_temp'] = item['temp']
            if item['temp'] > daily_data[date_key]['max_temp']:
                daily_data[date_key]['max_temp'] = item['temp']

        daily_data[date_key]['temps'].append(item['temp'])
        daily_data[date_key]['icons'].append(item['icon'])

    # Расчет средней температуры для каждого дня
    daily_forecast = []
    for date, values in daily_data.items():
        avg_temp = round(sum(values['temps']) / len(values['temps']), 1)
        # Наиболее частый значок за день
        icon = max(set(values['icons']), key=values['icons'].count)
        daily_forecast.append({
            'date': date,
            'avg_temp': avg_temp,
            'min_temp': round(values['min_temp'], 1),
            'max_temp': round(values['max_temp'], 1),
            'icon': icon
        })

    # Сортируем по дате
    daily_forecast.sort(key=lambda x: x['date'])

    avg_temp = round(sum(temps) / len(temps), 1)
    max_temp = round(max(temps), 1)
    min_temp = round(min(temps), 1)

    return dates, temps, avg_temp, min_temp, max_temp, weather_data, daily_forecast


def generate_plot(dates: list, temps: list, city: str) -> str:
    """Генерация графика в base64"""
    plt.figure(figsize=(10, 5))
    plt.plot(dates, temps, marker='o', linestyle='-', color='#3498db')
    plt.title(f'Прогноз температуры в {city}', fontsize=14)
    plt.xlabel('Дата и время', fontsize=12)
    plt.ylabel('Температура (°C)', fontsize=12)
    plt.grid(True, linestyle='--', alpha=0.7)
    plt.xticks(rotation=45, fontsize=10)
    plt.tight_layout()

    # Конвертация в base64
    buffer = BytesIO()
    plt.savefig(buffer, format='png', dpi=100)
    buffer.seek(0)
    plot_base64 = base64.b64encode(buffer.getvalue()).decode()
    plt.close()

    return plot_base64


@app.route('/weather/<city>')
def weather(city):
    try:
        # Получение и обработка данных
        data = get_weather_data(city)
        if data.get('cod') != '200':
            return render_template('weather.html',
                                   error=f"Город '{city}' не найден. Попробуйте другой город.")

        (dates, temps, avg_temp, min_temp,
         max_temp, hourly_forecast, daily_forecast) = process_weather_data(data)

        plot_url = generate_plot(dates, temps, city)

        # Основные данные о городе
        city_name = data['city']['name']
        country = data['city']['country']

        return render_template(
            'weather.html',
            city=city_name,
            country=country,
            plot_url=plot_url,
            avg_temp=avg_temp,
            min_temp=min_temp,
            max_temp=max_temp,
            hourly_forecast=hourly_forecast[:12],  # Только первые 12 часов
            daily_forecast=daily_forecast[:5]  # 5 дней
        )
    except Exception as e:
        return render_template('weather.html', error=f"Ошибка: {str(e)}")


if __name__ == '__main__':
    app.run(debug=True)