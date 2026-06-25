from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


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
    registry_version: str = REGISTRY_VERSION

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
    CitySettlementProfile("paris", "Paris", "LFPG", "Paris Charles de Gaulle Airport", "Europe/Paris", "C", 48.9962, 2.5979, "eu"),
    CitySettlementProfile("munich", "Munich", "EDDM", "Munich Airport", "Europe/Berlin", "C", 48.3537, 11.7750, "eu"),
    CitySettlementProfile("ankara", "Ankara", "LTAC", "Ankara Esenboga Airport", "Europe/Istanbul", "C", 40.1281, 32.9951, "eu"),
    CitySettlementProfile("seoul", "Seoul", "RKSI", "Incheon International Airport", "Asia/Seoul", "C", 37.4691, 126.4505, "asia"),
    CitySettlementProfile("tokyo", "Tokyo", "RJTT", "Tokyo Haneda Airport", "Asia/Tokyo", "C", 35.7647, 140.3864, "asia"),
    CitySettlementProfile("shanghai", "Shanghai", "ZSPD", "Shanghai Pudong International Airport", "Asia/Shanghai", "C", 31.1443, 121.8083, "asia"),
    CitySettlementProfile("singapore", "Singapore", "WSSS", "Singapore Changi Airport", "Asia/Singapore", "C", 1.3502, 103.9940, "asia"),
    CitySettlementProfile("lucknow", "Lucknow", "VILK", "Chaudhary Charan Singh International Airport", "Asia/Kolkata", "C", 26.7606, 80.8893, "asia"),
    CitySettlementProfile("tel-aviv", "Tel Aviv", "LLBG", "Ben Gurion Airport", "Asia/Jerusalem", "C", 32.0114, 34.8867, "asia"),
    CitySettlementProfile("toronto", "Toronto", "CYYZ", "Toronto Pearson International Airport", "America/Toronto", "C", 43.6772, -79.6306, "ca"),
    CitySettlementProfile("sao-paulo", "Sao Paulo", "SBGR", "Sao Paulo Guarulhos International Airport", "America/Sao_Paulo", "C", -23.4356, -46.4731, "sa"),
    CitySettlementProfile("buenos-aires", "Buenos Aires", "SAEZ", "Ezeiza International Airport", "America/Argentina/Buenos_Aires", "C", -34.8222, -58.5358, "sa"),
    CitySettlementProfile("wellington", "Wellington", "NZWN", "Wellington International Airport", "Pacific/Auckland", "C", -41.3272, 174.8052, "oc"),
)

SETTLEMENT_REGISTRY = {profile.city: profile for profile in _PROFILES}


def get_city_profile(city: str) -> CitySettlementProfile | None:
    return SETTLEMENT_REGISTRY.get(str(city or "").strip().lower())


def registry_payload() -> list[dict[str, Any]]:
    return [profile.to_dict() for profile in _PROFILES]

