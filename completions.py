import gzip
import os
from pathlib import Path
from typing import Any

import folium
import geopandas as gpd
import pandas as pd

MASS_BBOX = [(41.15, -73.57), (42.87, -69.83)]
COMPLETIONS_CSV = Path(__file__).parent / "completions.csv"
CDF = pd.read_csv(COMPLETIONS_CSV)


def _shape_style_func(x: Any) -> dict[str, str]:
    _color = "#888"
    town_name = x["properties"]["TOWN"].lower()
    if town_name in CDF["city"].str.lower().tolist():
        _color = "#04e300"
    return {"fillColor": _color, "color": _color, "weight": 1}


def create_city_map() -> folium.Map:
    access_token: str = os.getenv("MAPBOX_ACCESS_TOKEN")
    user_id: str = "mapbox"
    style_id: str = "outdoors-v12"
    folium_map = folium.Map(
        tiles=f"https://api.mapbox.com/styles/v1/{user_id}/{style_id}/tiles/{{z}}/{{x}}/{{y}}@2x?access_token={access_token}",
        attr="© Mapbox © OpenStreetMap",
    )
    folium_map.fit_bounds(MASS_BBOX)

    # Add shapes for municipalities
    gdf = gpd.read_file("TOWNSSURVEY_POLY.shp")
    completed_grp = folium.FeatureGroup(name="completed")
    not_completed_grp = folium.FeatureGroup(name="not completed")
    print("Coloring towns...")
    for town, town_df in gdf.groupby("TOWN"):
        _completed = town.lower() in CDF["city"].str.lower().tolist()
        if _completed:
            completed_grp.add_child(
                folium.GeoJson(town_df, style_function=_shape_style_func, tooltip=folium.Tooltip(town))
            )
        else:
            not_completed_grp.add_child(
                folium.GeoJson(town_df, style_function=_shape_style_func, tooltip=folium.Tooltip(town))
            )

    completed_grp.add_to(folium_map)
    not_completed_grp.add_to(folium_map)

    # Add GPX paths
    completed_track_grp = folium.FeatureGroup(name="completed tracks")
    for gpx in CDF[CDF["gpx_path"].notna()]["gpx_path"].unique():
        print("Adding ", gpx, " to the map...", sep="")
        filename = Path(gpx).name
        gpx_data = gpd.read_file(gpx, layer="tracks")
        geom = gpx_data.geometry.get_coordinates().values.tolist()
        geom = [[y, x] for x, y in geom]
        completed_track_grp.add_child(
            folium.PolyLine(geom, color="#1c25bc", weight=2, tooltip=folium.Tooltip(filename))
        )

    completed_track_grp.add_to(folium_map)

    folium.LayerControl().add_to(folium_map)
    return folium_map


if __name__ == "__main__":
    create_city_map().save("completions.html")

    # Gzip the file
    with open("completions.html", "rb") as f_in:
        with gzip.open("completions.html.gz", "wb") as f_out:
            f_out.write(f_in.read())
