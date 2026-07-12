from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse


REGISTRY_VERSION = "airport-settlement-registry-v1"


@dataclass(frozen=True)
class CitySettlementProfile:
    city: str
    city_name: str
    station_id: str
    station_name: str
    timezone: str
    unit: str
    latitude: float
    longitude: float
    region: str
    expected_metric: str = "highest_temperature"
    expected_resolution_provider: str = "polymarket_rule"
    verification_status: str = "provisional"
    city_scope: str = "market_candidate"
    registry_version: str = REGISTRY_VERSION
    location_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_PROFILES = (
    CitySettlementProfile("nyc", "New York City", "KLGA", "LaGuardia Airport", "America/New_York", "F", 40.7772, -73.8726, "us"),
    CitySettlementProfile("chicago", "Chicago", "KORD", "Chicago O'Hare International Airport", "America/Chicago", "F", 41.9742, -87.9073, "us"),
    CitySettlementProfile("miami", "Miami", "KMIA", "Miami International Airport", "America/New_York", "F", 25.7959, -80.2870, "us"),
    CitySettlementProfile("dallas", "Dallas", "KDAL", "Dallas Love Field", "America/Chicago", "F", 32.8471, -96.8518, "us"),
    CitySettlementProfile("seattle", "Seattle", "KSEA", "Seattle-Tacoma International Airport", "America/Los_Angeles", "F", 47.4502, -122.3088, "us"),
    CitySettlementProfile("atlanta", "Atlanta", "KATL", "Hartsfield-Jackson Atlanta International Airport", "America/New_York", "F", 33.6407, -84.4277, "us"),
    CitySettlementProfile("london", "London", "EGLC", "London City Airport", "Europe/London", "C", 51.5048, 0.0495, "eu"),
    CitySettlementProfile("paris", "Paris", "LFPB", "Paris-Le Bourget Airport", "Europe/Paris", "C", 48.967, 2.428, "eu"),
    CitySettlementProfile("munich", "Munich", "EDDM", "Munich Airport", "Europe/Berlin", "C", 48.3537, 11.7750, "eu"),
    CitySettlementProfile("ankara", "Ankara", "LTAC", "Ankara Esenboga Airport", "Europe/Istanbul", "C", 40.1281, 32.9951, "eu"),
    CitySettlementProfile("seoul", "Seoul", "RKSI", "Incheon International Airport", "Asia/Seoul", "C", 37.4691, 126.4505, "asia"),
    CitySettlementProfile("tokyo", "Tokyo", "RJTT", "Tokyo Haneda Airport", "Asia/Tokyo", "C", 35.5530, 139.7810, "asia", location_version=2),
    CitySettlementProfile("shanghai", "Shanghai", "ZSPD", "Shanghai Pudong International Airport", "Asia/Shanghai", "C", 31.1443, 121.8083, "asia"),
    CitySettlementProfile("beijing", "Beijing", "ZBAA", "Beijing Capital International Airport", "Asia/Shanghai", "C", 40.0799, 116.6031, "asia"),
    CitySettlementProfile("wuhan", "Wuhan", "ZHHH", "Wuhan Tianhe International Airport", "Asia/Shanghai", "C", 30.7838, 114.2081, "asia"),
    CitySettlementProfile("qingdao", "Qingdao", "ZSQD", "Qingdao Jiaodong International Airport", "Asia/Shanghai", "C", 36.3619, 120.0881, "asia"),
    CitySettlementProfile("shenzhen", "Shenzhen", "ZGSZ", "Shenzhen Bao'an International Airport", "Asia/Shanghai", "C", 22.6393, 113.8107, "asia"),
    CitySettlementProfile("taipei", "Taipei", "RCSS", "Taipei Songshan Airport", "Asia/Taipei", "C", 25.0694, 121.5519, "asia"),
    CitySettlementProfile("hong-kong", "Hong Kong", "VHHH", "Hong Kong International Airport", "Asia/Hong_Kong", "C", 22.3080, 113.9185, "asia"),
    CitySettlementProfile("singapore", "Singapore", "WSSS", "Singapore Changi Airport", "Asia/Singapore", "C", 1.3502, 103.9940, "asia"),
    CitySettlementProfile("lucknow", "Lucknow", "VILK", "Chaudhary Charan Singh International Airport", "Asia/Kolkata", "C", 26.7606, 80.8893, "asia"),
    CitySettlementProfile("tel-aviv", "Tel Aviv", "LLBG", "Ben Gurion Airport", "Asia/Jerusalem", "C", 32.0114, 34.8867, "asia"),
    CitySettlementProfile("toronto", "Toronto", "CYYZ", "Toronto Pearson International Airport", "America/Toronto", "C", 43.6772, -79.6306, "ca"),
    CitySettlementProfile("sao-paulo", "Sao Paulo", "SBGR", "Sao Paulo Guarulhos International Airport", "America/Sao_Paulo", "C", -23.4356, -46.4731, "sa"),
    CitySettlementProfile("buenos-aires", "Buenos Aires", "SAEZ", "Ezeiza International Airport", "America/Argentina/Buenos_Aires", "C", -34.8222, -58.5358, "sa"),
    CitySettlementProfile("wellington", "Wellington", "NZWN", "Wellington International Airport", "Pacific/Auckland", "C", -41.3272, 174.8052, "oc"),
    # PolyWX observation catalog parity. These profiles are display-visible but
    # remain collector-disabled and trading-ineligible until their own source
    # smoke tests and settlement contracts are verified.
    CitySettlementProfile("austin", "Austin", "KAUS", "Austin-Bergstrom International Airport", "America/Chicago", "F", 30.1831, -97.6806, "us", city_scope="observation_only"),
    CitySettlementProfile("denver", "Denver", "KDEN", "Denver International Airport", "America/Denver", "F", 39.8466, -104.6562, "us", city_scope="observation_only"),
    CitySettlementProfile("houston", "Houston", "KHOU", "William P. Hobby Airport", "America/Chicago", "F", 29.6458, -95.2821, "us", city_scope="observation_only"),
    CitySettlementProfile("los-angeles", "Los Angeles", "KLAX", "Los Angeles International Airport", "America/Los_Angeles", "F", 33.9382, -118.3866, "us", city_scope="observation_only"),
    CitySettlementProfile("san-francisco", "San Francisco", "KSFO", "San Francisco International Airport", "America/Los_Angeles", "F", 37.6196, -122.3656, "us", city_scope="observation_only"),
    CitySettlementProfile("chongqing", "Chongqing", "ZUCK", "Chongqing Jiangbei International Airport", "Asia/Shanghai", "C", 29.7180, 106.6390, "asia", city_scope="observation_only"),
    CitySettlementProfile("chengdu", "Chengdu", "ZUUU", "Chengdu Shuangliu International Airport", "Asia/Shanghai", "C", 30.5760, 103.9500, "asia", city_scope="observation_only"),
    CitySettlementProfile("guangzhou", "Guangzhou", "ZGGG", "Guangzhou Baiyun International Airport", "Asia/Shanghai", "C", 23.3920, 113.3070, "asia", city_scope="observation_only"),
    CitySettlementProfile("jakarta", "Jakarta", "WIHH", "Halim Perdanakusuma International Airport", "Asia/Jakarta", "C", -6.2670, 106.8910, "asia", city_scope="observation_only"),
    CitySettlementProfile("jeddah", "Jeddah", "OEJN", "King Abdulaziz International Airport", "Asia/Riyadh", "C", 21.6850, 39.1660, "asia", city_scope="observation_only"),
    CitySettlementProfile("karachi", "Karachi", "OPKC", "Jinnah International Airport", "Asia/Karachi", "C", 24.9020, 67.1390, "asia", city_scope="observation_only"),
    CitySettlementProfile("busan", "Busan", "RKPK", "Gimhae International Airport", "Asia/Seoul", "C", 35.1790, 128.9380, "asia", city_scope="observation_only"),
    CitySettlementProfile("kuala-lumpur", "Kuala Lumpur", "WMKK", "Kuala Lumpur International Airport", "Asia/Kuala_Lumpur", "C", 2.7470, 101.7140, "asia", city_scope="observation_only"),
    CitySettlementProfile("manila", "Manila", "RPLL", "Ninoy Aquino International Airport", "Asia/Manila", "C", 14.5070, 121.0040, "asia", city_scope="observation_only"),
    CitySettlementProfile("amsterdam", "Amsterdam", "EHAM", "Amsterdam Airport Schiphol", "Europe/Amsterdam", "C", 52.3150, 4.7900, "eu", city_scope="observation_only"),
    CitySettlementProfile("helsinki", "Helsinki", "EFHK", "Helsinki-Vantaa Airport", "Europe/Helsinki", "C", 60.3270, 24.9570, "eu", city_scope="observation_only"),
    CitySettlementProfile("istanbul", "Istanbul", "LTFM", "Istanbul Airport", "Europe/Istanbul", "C", 41.2620, 28.7400, "eu", city_scope="observation_only"),
    CitySettlementProfile("madrid", "Madrid", "LEMD", "Adolfo Suarez Madrid-Barajas Airport", "Europe/Madrid", "C", 40.4660, -3.5550, "eu", city_scope="observation_only"),
    CitySettlementProfile("milan", "Milan", "LIMC", "Milan Malpensa Airport", "Europe/Rome", "C", 45.6310, 8.7280, "eu", city_scope="observation_only"),
    CitySettlementProfile("moscow", "Moscow", "UUWW", "Vnukovo International Airport", "Europe/Moscow", "C", 55.5920, 37.2610, "eu", city_scope="observation_only"),
    CitySettlementProfile("warsaw", "Warsaw", "EPWA", "Warsaw Chopin Airport", "Europe/Warsaw", "C", 52.1630, 20.9610, "eu", city_scope="observation_only"),
    CitySettlementProfile("cape-town", "Cape Town", "FACT", "Cape Town International Airport", "Africa/Johannesburg", "C", -33.9650, 18.6020, "africa", city_scope="observation_only"),
    CitySettlementProfile("lagos", "Lagos", "DNMM", "Murtala Muhammed International Airport", "Africa/Lagos", "C", 6.5770, 3.3210, "africa", city_scope="observation_only"),
    CitySettlementProfile("mexico-city", "Mexico City", "MMMX", "Mexico City International Airport", "America/Mexico_City", "C", 19.4360, -99.0720, "na", city_scope="observation_only"),
    CitySettlementProfile("panama-city", "Panama City", "MPMG", "Albrook Marcos A. Gelabert Airport", "America/Panama", "C", 8.9670, -79.5550, "na", city_scope="observation_only"),
)

SETTLEMENT_REGISTRY = {profile.city: profile for profile in _PROFILES}


def get_city_profile(city: str) -> CitySettlementProfile | None:
    return SETTLEMENT_REGISTRY.get(str(city or "").strip().lower())


def registry_payload() -> list[dict[str, Any]]:
    return [profile.to_dict() for profile in _PROFILES]


def forecast_source_matches_profile_location(
    source_url: str | None,
    profile: CitySettlementProfile | None,
    *,
    tolerance_degrees: float = 0.02,
) -> bool:
    """Reject a forecast only when its URL proves it used another location."""
    if not source_url or profile is None:
        return True
    query = parse_qs(urlparse(str(source_url)).query)
    values = query.get("geocode") or []
    try:
        if values:
            latitude, longitude = (float(part.strip()) for part in values[0].split(",", 1))
        elif query.get("latitude") and query.get("longitude"):
            latitude = float(query["latitude"][0])
            longitude = float(query["longitude"][0])
        else:
            return True
    except (IndexError, TypeError, ValueError):
        return True
    tolerance = max(0.0, float(tolerance_degrees))
    return (
        abs(latitude - float(profile.latitude)) <= tolerance
        and abs(longitude - float(profile.longitude)) <= tolerance
    )
