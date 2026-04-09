import os
from collections import namedtuple
from functools import lru_cache
from pathlib import Path
from typing import Any

from more_itertools import chunked

MASS_GOV = "https://www.mass.gov/lists/massachusetts-city-and-town-websites"
CITIES_CSV = Path(__file__).parent / "cities.csv"
GMAPS_API_KEY = os.getenv("GMAPS_API_KEY")


def get_mass_cities() -> list[str]:
    import requests
    from bs4 import BeautifulSoup

    response = requests.get(MASS_GOV)
    soup = BeautifulSoup(response.text, "html.parser")

    # Find all div's with class "ma__download-link"
    download_links = soup.find_all("div", class_="ma__download-link")
    return [link.text.strip() for link in download_links if link.text.strip()]


@lru_cache
def get_google_maps_client() -> Any:
    import googlemaps

    return googlemaps.Client(key=GMAPS_API_KEY)


Distance = namedtuple("Distance", ["distance", "duration"])
StandardDistance = namedtuple("StandardDistance", ["distance_mi", "duration_min"])


def standardize_distance(distance: Distance) -> StandardDistance:
    """
    Convert Distance namedtuple to StandardDistance with distance in miles and duration in minutes.
    """
    if distance.distance is None or distance.duration is None:
        return StandardDistance(distance_mi=None, duration_min=None)

    # Extract numeric values from the distance and duration strings
    distance_mi = float(distance.distance.replace(" mi", ""))
    hours, minutes = 0, 0

    # Parse a string like "x hour(s) y min(s)"
    if " hour" not in distance.duration:
        minutes = int(distance.duration.replace(" mins", "").replace(" min", ""))
    else:
        duration_1, duration_2 = distance.duration.split(" hour")
        duration_2 = duration_2.lstrip("s ")
        hours = int(duration_1.strip())
        if "min" in duration_2:
            minutes = int(duration_2.replace(" mins", "").replace(" min", ""))

    duration_min = hours * 60 + minutes

    return StandardDistance(distance_mi=distance_mi, duration_min=duration_min)


def get_distances_to_woburn(cities: list[str]) -> dict[str, StandardDistance]:

    # Initialize Google Maps client
    gmaps = get_google_maps_client()

    # Get distance from city to Woburn
    woburn_ma = "200 Presidential Way, Woburn, MA"

    distances = {}

    for city_chunk in chunked(cities, 20):
        cities_ma = [f"{city}, MA" for city in city_chunk]

        distance_result = gmaps.distance_matrix(
            origins=woburn_ma, destinations=cities_ma, mode="driving", units="imperial"
        )
        if distance_result["status"] != "OK":
            raise ValueError(f"Error fetching distance: {distance_result['error_message']}")

        for idx, city in enumerate(city_chunk):
            gmaps_distance = None
            gmaps_duration = None
            if "distance" in distance_result["rows"][0]["elements"][idx]:
                gmaps_distance = distance_result["rows"][0]["elements"][idx]["distance"]["text"]
            if "duration" in distance_result["rows"][0]["elements"][idx]:
                gmaps_duration = distance_result["rows"][0]["elements"][idx]["duration"]["text"]
            distances[city] = standardize_distance(Distance(distance=gmaps_distance, duration=gmaps_duration))

    return distances


def dump_city_distances_to_csv(cities: list[str], output_file: Path) -> None:
    import csv

    distances = get_distances_to_woburn(cities)

    with output_file.open("w") as csvfile:
        writer = csv.DictWriter(
            csvfile, fieldnames=["city", "distance_mi", "duration_min"], quoting=csv.QUOTE_NONNUMERIC
        )
        writer.writeheader()
        for city in cities:
            if city in distances:
                distance_info = distances[city]
                writer.writerow(
                    {"city": city, "distance_mi": distance_info.distance_mi, "duration_min": distance_info.duration_min}
                )
            else:
                writer.writerow({"city": city, "distance_mi": "N/A", "duration_min": "N/A"})


def main() -> None:
    """
    Main entrypoint.
    """

    # Step 1: fetch list of cities in Mass
    cities = get_mass_cities()
    print(f"Found {len(cities)} cities.")

    # Step 2: use googlemaps to get the distance between each city and Woburn, dumping the results to a CSV file
    dump_city_distances_to_csv(cities, CITIES_CSV)
    print(f"Distances written to {CITIES_CSV}")


if __name__ == "__main__":
    main()
