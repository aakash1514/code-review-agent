from app.weather import WeatherClient


def temperature_widget(client: WeatherClient, city):
    resp = client.current(city)
    if resp.status_code != 200:
        return {"city": city, "temp": None}
    return {"city": city, "temp": resp.json()["main"]["temp"]}


def wind_widget(client: WeatherClient, city):
    data = client.current(city).json()
    return {"city": city, "wind": data["wind"]["speed"]}
