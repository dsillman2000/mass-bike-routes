import gzip
import os
from math import cos, radians, sqrt
from pathlib import Path
from typing import Any

import folium
import geopandas as gpd
import pandas as pd

MASS_BBOX = [(41.49, -73.09), (42.80, -69.59)]
COMPLETIONS_CSV = Path(__file__).parent / "completions.csv"
CDF = pd.read_csv(COMPLETIONS_CSV)

TOWN_DEFAULT_COLOR = "#888888"
TOWN_COMPLETED_COLOR = "#04E300"
TOWN_PENDING_COLOR = "#F59E0B"
ROUTE_COMPLETED_COLOR = "#1C25BC"
ROUTE_PENDING_COLOR = "#D97706"

_NORMALIZED_CDF = CDF.assign(
    city=CDF["city"].astype(str).str.casefold(),
    completed=CDF["completed"].fillna(0).astype(bool),
)
CITY_COMPLETION = _NORMALIZED_CDF.groupby("city")["completed"].any().to_dict()
ROUTE_COMPLETION = _NORMALIZED_CDF[_NORMALIZED_CDF["gpx_path"].notna()].groupby("gpx_path")["completed"].all().to_dict()


def _town_color(town_name: str) -> str:
    completion_status = CITY_COMPLETION.get(town_name.casefold())
    if completion_status is True:
        return TOWN_COMPLETED_COLOR
    if completion_status is False:
        return TOWN_PENDING_COLOR
    return TOWN_DEFAULT_COLOR


def _shape_style_func(x: Any) -> dict[str, Any]:
    color = _town_color(x["properties"]["TOWN"])
    return {"fillColor": color, "color": color, "weight": 1}


def _load_track_points(gpx_path: str) -> gpd.GeoDataFrame:
    """Load and normalize the point-by-point elevation samples for a GPX track.

    Parameters
    ----------
    gpx_path:
        Path to a GPX file that contains a `track_points` layer.

    Returns
    -------
    geopandas.GeoDataFrame
        The raw GPX point samples sorted into track order so downstream slope
        calculations always examine consecutive points in the correct sequence.
    """
    return (
        gpd.read_file(gpx_path, layer="track_points")
        .sort_values(["track_fid", "track_seg_id", "track_seg_point_id"])
        .reset_index(drop=True)
    )


def _distance_meters(start: Any, end: Any) -> float:
    """Approximate horizontal distance between two GPS points in meters.

    This helper uses an equirectangular approximation on WGS84 longitude and
    latitude coordinates. The approximation is intentionally lightweight and is
    more than accurate enough for the short point-to-point segments that appear
    in GPX track logs.

    Parameters
    ----------
    start, end:
        Point-like geometries with `.x` longitude and `.y` latitude attributes.

    Returns
    -------
    float
        The ground distance in meters, ignoring elevation. Very small values are
        expected when the GPS recorder emitted duplicate or nearly duplicate
        points, and those short segments can later be filtered out as noise.
    """
    earth_radius_m = 6_371_000
    avg_latitude_radians = radians((start.y + end.y) / 2)
    x_component = radians(end.x - start.x) * cos(avg_latitude_radians)
    y_component = radians(end.y - start.y)
    return earth_radius_m * sqrt(x_component**2 + y_component**2)


def find_extreme_slopes(
    track_points: gpd.GeoDataFrame,
    min_slope_pct: float = 3.0,
    max_annotations_per_direction: int = 1,
    min_segment_length_m: float = 20.0,
) -> list[dict[str, Any]]:
    """Identify the steepest uphill and downhill sections of a route.

    The analysis walks through consecutive GPS samples in the GPX
    `track_points` layer, computes elevation change over horizontal ground
    distance, and ranks the resulting grades. Only segments that are long enough
    to be trustworthy and steep enough to be meaningful are returned, which
    keeps the annotations useful for a human reader instead of highlighting GPS
    jitter.

    Parameters
    ----------
    track_points:
        A GeoDataFrame from the GPX `track_points` layer. It should contain
        point geometries in longitude/latitude coordinates and an `ele` column
        measured in meters.
    min_slope_pct:
        Minimum absolute grade, expressed as a percentage, before a segment is
        considered annotation-worthy. For example, `6.0` means the road must
        rise or fall at least 6 meters over 100 meters of horizontal travel.
    max_annotations_per_direction:
        Maximum number of uphill and downhill markers to return. The default of
        `1` keeps the map uncluttered by showing only the single strongest climb
        and single strongest descent.
    min_segment_length_m:
        Minimum horizontal length for a candidate segment. This suppresses false
        positives from duplicate points, GPS wobble, or tiny pauses in the log.

    Returns
    -------
    list[dict[str, Any]]
        A list of annotation records ready for rendering. Each record contains:

        - `location`: a `(lat, lon)` tuple placed at the segment midpoint
        - `label`: a Unicode arrow, `↑` for uphill or `↓` for downhill
        - `tooltip_text`: a human-readable summary of the slope and segment
        - `slope_pct`: the signed grade percentage used for sorting

    Notes
    -----
    The function analyzes local slopes between adjacent recorded points instead
    of averaging across the full ride. That makes it much better at flagging the
    short, punchy climbs and descents that riders actually notice on the route.
    """
    if track_points.empty or "ele" not in track_points.columns:
        return []

    elevation_points = track_points[track_points["ele"].notna()].copy()
    if len(elevation_points) < 2:
        return []

    candidates: list[dict[str, Any]] = []
    previous_point: Any | None = None

    for point in elevation_points.itertuples():
        if (
            previous_point is not None
            and previous_point.geometry is not None
            and point.geometry is not None
            and previous_point.track_fid == point.track_fid
            and previous_point.track_seg_id == point.track_seg_id
        ):
            horizontal_distance_m = _distance_meters(previous_point.geometry, point.geometry)
            if horizontal_distance_m < min_segment_length_m:
                previous_point = point
                continue

            elevation_change_m = float(point.ele) - float(previous_point.ele)
            slope_pct = (elevation_change_m / horizontal_distance_m) * 100
            if abs(slope_pct) >= min_slope_pct:
                midpoint = (
                    (previous_point.geometry.y + point.geometry.y) / 2,
                    (previous_point.geometry.x + point.geometry.x) / 2,
                )
                direction_text = "steep climb" if slope_pct > 0 else "steep descent"
                candidates.append(
                    {
                        "location": midpoint,
                        "label": "↑" if slope_pct > 0 else "↓",
                        "tooltip_text": (f"{direction_text}: {slope_pct:+.1f}% over {horizontal_distance_m:.0f} m"),
                        "slope_pct": slope_pct,
                    }
                )
        previous_point = point

    steepest_uphill = sorted(
        [candidate for candidate in candidates if candidate["slope_pct"] > 0],
        key=lambda candidate: candidate["slope_pct"],
        reverse=True,
    )[:max_annotations_per_direction]
    steepest_downhill = sorted(
        [candidate for candidate in candidates if candidate["slope_pct"] < 0],
        key=lambda candidate: candidate["slope_pct"],
    )[:max_annotations_per_direction]

    return [*steepest_uphill, *steepest_downhill]


def _letter_marker(location: tuple[float, float], label: str, color: str, tooltip_text: str) -> folium.Marker:
    """Create a compact text marker for route annotations.

    The marker uses a small colored badge rendered through `DivIcon`, which lets
    us display plain text such as `H`, `L`, `↑`, or `↓` directly on the map.
    """
    width = 18 if len(label) == 1 else 28
    return folium.Marker(
        location=location,
        tooltip=folium.Tooltip(tooltip_text),
        icon=folium.DivIcon(
            html=(
                f'<div style="display:flex;align-items:center;justify-content:center;'
                f"width:{width}px;height:18px;border-radius:999px;background:{color};"
                "color:white;font-size:11px;font-weight:700;"
                'border:1px solid white;box-shadow:0 0 2px rgba(0,0,0,0.35);">'
                f"{label}</div>"
            ),
            icon_size=(width, 18),
            icon_anchor=(width // 2, 9),
            class_name="empty",
        ),
    )


def _start_marker(location: tuple[float, float], color: str, tooltip_text: str) -> folium.CircleMarker:
    """Create a plain circular marker for the start of a route."""
    return folium.CircleMarker(
        location=location,
        radius=5,
        color=color,
        fill=True,
        fill_color=color,
        fill_opacity=1.0,
        weight=2,
        tooltip=folium.Tooltip(tooltip_text),
    )


def _build_route_layers(gpx_path: str, track_points: gpd.GeoDataFrame, route_is_completed: bool) -> list[Any]:
    filename = Path(gpx_path).name
    line_color = ROUTE_COMPLETED_COLOR if route_is_completed else ROUTE_PENDING_COLOR

    gpx_data = gpd.read_file(gpx_path, layer="tracks")
    geom = gpx_data.geometry.get_coordinates().values.tolist()
    geom = [[y, x] for x, y in geom]
    route_layers: list[Any] = [
        folium.PolyLine(geom, color=line_color, weight=3, opacity=0.9, tooltip=folium.Tooltip(filename))
    ]

    start_location: tuple[float, float] | None = None
    start_tooltip = "Start"
    if not track_points.empty and track_points.iloc[0].geometry is not None:
        start_location = (track_points.iloc[0].geometry.y, track_points.iloc[0].geometry.x)
        if "ele" in track_points.columns and pd.notna(track_points.iloc[0]["ele"]):
            start_tooltip = f"Start: {float(track_points.iloc[0]['ele']):.0f} m"
    elif geom:
        start_location = tuple(geom[0])

    if start_location is not None:
        route_layers.append(
            _start_marker(
                location=start_location,
                color=line_color,
                tooltip_text=start_tooltip,
            )
        )

    if track_points.empty or "ele" not in track_points.columns or track_points["ele"].dropna().empty:
        return route_layers

    elevation_points = track_points[track_points["ele"].notna()].copy()
    low_point = elevation_points.loc[elevation_points["ele"].idxmin()]
    high_point = elevation_points.loc[elevation_points["ele"].idxmax()]

    if low_point.geometry.equals(high_point.geometry) and float(low_point["ele"]) == float(high_point["ele"]):
        route_layers.append(
            _letter_marker(
                location=(low_point.geometry.y, low_point.geometry.x),
                label="H/L",
                color=line_color,
                tooltip_text=f"Low/High: {float(low_point['ele']):.0f} m",
            )
        )
        return route_layers

    route_layers.extend(
        [
            _letter_marker(
                location=(low_point.geometry.y, low_point.geometry.x),
                label="L",
                color=line_color,
                tooltip_text=f"Low: {float(low_point['ele']):.0f} m",
            ),
            _letter_marker(
                location=(high_point.geometry.y, high_point.geometry.x),
                label="H",
                color=line_color,
                tooltip_text=f"High: {float(high_point['ele']):.0f} m",
            ),
        ]
    )

    for slope_annotation in find_extreme_slopes(track_points):
        route_layers.append(
            _letter_marker(
                location=slope_annotation["location"],
                label=slope_annotation["label"],
                color=line_color,
                tooltip_text=f"{slope_annotation['tooltip_text']}",
            )
        )

    return route_layers


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
    completed_grp = folium.FeatureGroup(name="completed towns")
    not_completed_grp = folium.FeatureGroup(name="planned towns")
    other_towns_grp = folium.FeatureGroup(name="other towns")
    print("Coloring towns...")
    for town, town_df in gdf.groupby("TOWN"):
        town_status = CITY_COMPLETION.get(town.casefold())
        town_layer = folium.GeoJson(town_df, style_function=_shape_style_func, tooltip=folium.Tooltip(town))
        if town_status is True:
            completed_grp.add_child(town_layer)
        elif town_status is False:
            not_completed_grp.add_child(town_layer)
        else:
            other_towns_grp.add_child(town_layer)

    completed_grp.add_to(folium_map)
    not_completed_grp.add_to(folium_map)
    other_towns_grp.add_to(folium_map)

    # Add GPX paths
    completed_track_grp = folium.FeatureGroup(name="completed tracks")
    not_completed_track_grp = folium.FeatureGroup(name="planned tracks")
    route_paths = CDF[CDF["gpx_path"].notna()]["gpx_path"].unique()
    track_points_by_gpx = {gpx: _load_track_points(gpx) for gpx in route_paths}

    for gpx in route_paths:
        print("Adding ", gpx, " to the map...", sep="")
        route_is_completed = ROUTE_COMPLETION.get(gpx, False)
        route_layers = _build_route_layers(gpx, track_points_by_gpx[gpx], route_is_completed)
        target_group = completed_track_grp if route_is_completed else not_completed_track_grp
        for route_layer in route_layers:
            target_group.add_child(route_layer)

    completed_track_grp.add_to(folium_map)
    not_completed_track_grp.add_to(folium_map)

    folium.LayerControl().add_to(folium_map)
    return folium_map


if __name__ == "__main__":
    create_city_map().save("completions.html")

    # Gzip the file
    with open("completions.html", "rb") as f_in:
        with gzip.open("completions.html.gz", "wb") as f_out:
            f_out.write(f_in.read())
