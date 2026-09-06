"""Disabled-by-caller CTF/V2 BUY/GTC transport, without DB or environment reads.

Audited against Polymarket/py-clob-client-v2 tag v1.1.0 (2026-07-17).
The newer unified polymarket-client 0.9.0 is NOT this adapter's dependency:
its public secure-client factory can deploy a deposit wallet automatically.
This adapter never derives credentials, deploys wallets, approves allowances,
retries submissions, or starts a background task. The executor owns live gates,
durable pre-submit idempotency, reservations, and reconciliation after restart.

Persist PreparedOrder.order_hash and public fields BEFORE submit_prepared().
Never serialize/log PreparedOrder.signed_json or the SDK client/credentials.
An unknown submission/cancellation retains exposure until reconciled; a 404 is
not proof an order never existed. Call synchronous methods off the API loop.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Callable
from urllib.parse import quote as urlquote

import requests


SDK_PACKAGE = "py-clob-client-v2"
SDK_VERSION = "1.1.0"
CLOB_HOST = "https://clob.polymarket.com"
GAMMA_HOST = "https://gamma-api.polymarket.com"
GEOBLOCK_URL = "https://polymarket.com/api/geoblock"
_UNIT = Decimal(1_000_000)
_TICKS = frozenset(map(Decimal, ("0.1", "0.01", "0.005", "0.0025", "0.001", "0.0001")))
_ORDER_HASH = re.compile(r"0x[0-9a-fA-F]{64}\Z")


class TradingTransportError(RuntimeError):
    """A stable reason code only; never includes upstream bodies or key material."""


class _HttpFailure(TradingTransportError):
    def __init__(self, status_code: int):
        super().__init__("http_request_failed")
        self.status_code = status_code


@dataclass(frozen=True)
class TradingCredentials:
    private_key: str = field(repr=False)
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    api_passphrase: str = field(repr=False)
    funder: str
    signature_type: int


@dataclass(frozen=True)
class PreparedOrder:
    order_hash: str
    token_id: str
    price: str
    size: str
    amount: str
    neg_risk: bool
    tick_size: str
    prepared_at: float
    quote_timestamp: float
    signed_json: str = field(repr=False)

    @property
    def order_id(self) -> str:
        """Compatibility alias for the executor's pre-submit database write."""
        return self.order_hash

    def public_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in (
            "order_hash", "token_id", "price", "size", "amount", "neg_risk",
            "tick_size", "prepared_at", "quote_timestamp",
        )}


def _decimal(value: Any, reason: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise TradingTransportError(reason)
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise TradingTransportError(reason) from None
    if not result.is_finite() or result < 0 or (positive and result == 0):
        raise TradingTransportError(reason)
    return result


def _units(value: Any, reason: str) -> Decimal:
    result = _decimal(value, reason)
    if result != result.to_integral_value():
        raise TradingTransportError(reason)
    return result / _UNIT


def _timestamp(value: Any) -> float:
    if isinstance(value, bool) or value in (None, ""):
        raise TradingTransportError("book_timestamp_invalid")
    try:
        if str(value).isdigit():
            return int(str(value)) / 1000
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        raise TradingTransportError("book_timestamp_invalid") from None


def _list(value: Any, reason: str) -> list:
    try:
        result = json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        raise TradingTransportError(reason) from None
    if not isinstance(result, list):
        raise TradingTransportError(reason)
    return result


def _levels(value: Any) -> list[dict[str, str]]:
    levels = []
    for row in _list(value, "book_levels_invalid"):
        if not isinstance(row, dict):
            raise TradingTransportError("book_levels_invalid")
        price = _decimal(row.get("price"), "book_price_invalid", positive=True)
        size = _decimal(row.get("size"), "book_size_invalid", positive=True)
        if price >= 1:
            raise TradingTransportError("book_price_invalid")
        levels.append({"price": str(price), "size": str(size)})
    if not levels:
        raise TradingTransportError("book_side_absent")
    return levels


class _JsonSession:
    """One egress for geographic and trading requests; no redirects or retries."""

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.mount("https://", requests.adapters.HTTPAdapter(max_retries=0))

    def request(self, method: str, url: str, *, headers=None, data=None, params=None):
        if not (url.startswith(CLOB_HOST + "/") or url.startswith(GAMMA_HOST + "/") or url == GEOBLOCK_URL):
            raise TradingTransportError("untrusted_endpoint")
        try:
            request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
            request_headers.update(headers or {})
            response = self.session.request(
                method, url, headers=request_headers, data=data, params=params,
                timeout=(5, 10), allow_redirects=False,
            )
        except requests.RequestException:
            raise TradingTransportError("network_request_failed") from None
        if response.status_code != 200:
            raise _HttpFailure(response.status_code)
        try:
            return response.json()
        except ValueError:
            raise TradingTransportError("response_json_invalid") from None

    def close(self) -> None:
        self.session.close()


class _SdkBackend:
    def __init__(self, credentials: TradingCredentials) -> None:
        try:
            installed = version(SDK_PACKAGE)
        except PackageNotFoundError:
            raise TradingTransportError("live_sdk_not_installed") from None
        if installed != SDK_VERSION:
            raise TradingTransportError("live_sdk_version_mismatch")
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds
        from py_clob_client_v2.config import get_contract_config

        self.http = _JsonSession()
        http = self.http

        # These pinned SDK hooks isolate HTTP from its global client/body logger.
        # Signing, authentication headers, payloads and order hashes remain SDK-owned.
        class SingleAttemptClient(ClobClient):
            def _get(self, endpoint, headers=None, params=None):
                return http.request("GET", endpoint, headers=headers, params=params)

            def _post(self, endpoint, headers=None, data=None, params=None):
                return http.request("POST", endpoint, headers=headers, data=data, params=params)

            def _delete(self, endpoint, headers=None, data=None, params=None):
                return http.request("DELETE", endpoint, headers=headers, data=data, params=params)

            def _resolve_transactions_hashes(self, response):
                # The caller's reconciler follows tradeIDs; do not poll inside submit.
                return response

        try:
            self.client = SingleAttemptClient(
                CLOB_HOST, chain_id=137, key=credentials.private_key,
                creds=ApiCreds(credentials.api_key, credentials.api_secret, credentials.api_passphrase),
                signature_type=credentials.signature_type, funder=credentials.funder,
                retry_on_error=False, use_server_time=False,
            )
            if credentials.signature_type == 0 and self.client.signer.address().lower() != credentials.funder.lower():
                raise TradingTransportError("eoa_funder_mismatch")
            self.contracts = get_contract_config(137)
        except Exception:
            self.http.close()
            raise TradingTransportError("client_configuration_invalid") from None

    def public_json(self, url: str, params=None):
        return self.http.request("GET", url, params=params)

    def exchange(self, neg_risk: bool) -> str:
        return self.contracts.neg_risk_exchange_v2 if neg_risk else self.contracts.exchange_v2

    def balance(self):
        from py_clob_client_v2.clob_types import AssetType, BalanceAllowanceParams
        return self.client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))

    def _builder(self, neg_risk: bool):
        from py_clob_client_v2.order_utils.exchange_order_builder_v2 import ExchangeOrderBuilderV2
        return ExchangeOrderBuilderV2(self.exchange(neg_risk), 137, self.client.signer)

    def sign(self, token_id: str, price: Decimal, size: Decimal, neg_risk: bool):
        from py_clob_client_v2.order_utils.model.order_data_v2 import OrderDataV2
        builder = self._builder(neg_risk)
        account = self.client.builder
        order = builder.build_signed_order(OrderDataV2(
            maker=account.funder, tokenId=token_id,
            makerAmount=str(int(price * size * _UNIT)), takerAmount=str(int(size * _UNIT)),
            side=0, signer=account.funder if int(account.signature_type) == 3 else self.client.signer.address(),
            signatureType=account.signature_type, expiration="0",
        ))
        return builder.build_order_hash(builder.build_order_typed_data(order)), json.dumps(asdict(order))

    def restore(self, prepared: PreparedOrder):
        from py_clob_client_v2.order_utils.model.order_data_v2 import SignedOrderV2
        order = SignedOrderV2(**json.loads(prepared.signed_json))
        builder = self._builder(prepared.neg_risk)
        if builder.build_order_hash(builder.build_order_typed_data(order)) != prepared.order_hash:
            raise TradingTransportError("prepared_hash_mismatch")
        if (int(order.side) != 0 or int(order.expiration) != 0 or order.tokenId != prepared.token_id
                or int(order.makerAmount) != int(Decimal(prepared.amount) * _UNIT)
                or int(order.takerAmount) != int(Decimal(prepared.size) * _UNIT)
                or order.maker.lower() != self.client.builder.funder.lower()):
            raise TradingTransportError("prepared_order_mismatch")
        return order

    def post(self, order):
        from py_clob_client_v2.clob_types import OrderType
        return self.client.post_order(order, order_type=OrderType.GTC, post_only=False, defer_exec=False)

    def get_order(self, order_id: str):
        return self.client.get_order(order_id)

    def cancel_order(self, order_id: str):
        from py_clob_client_v2.clob_types import OrderPayload
        return self.client.cancel_order(OrderPayload(orderID=order_id))

    def close(self):
        self.http.close()


class ClobTradingTransport:
    """Explicitly constructed by an authorized caller; construction makes no requests.

    preflight() DOES make an authenticated read of balance/allowance, after the
    public geography and market checks pass. Never call it in offline dry runs.
    No method changes production_ready or reads config/.env. This is not a risk
    engine: freshness here does not prove forecast/strategy/live eligibility.
    """

    def __init__(self, credentials: TradingCredentials | None = None, *,
                 backend=None, clock: Callable[[], float] = time.time,
                 max_book_age_seconds: float = 30.0, future_skew_seconds: float = 2.0):
        if (not math.isfinite(max_book_age_seconds) or not math.isfinite(future_skew_seconds)
                or max_book_age_seconds <= 0 or future_skew_seconds < 0):
            raise TradingTransportError("freshness_policy_invalid")
        if backend is None:
            if not isinstance(credentials, TradingCredentials):
                raise TradingTransportError("credentials_required")
            if not all(isinstance(v, str) and v.strip() for v in (
                    credentials.private_key, credentials.api_key, credentials.api_secret, credentials.api_passphrase)):
                raise TradingTransportError("credentials_incomplete")
            if (type(credentials.signature_type) is not int or credentials.signature_type not in (0, 1, 2, 3)
                    or not re.fullmatch(r"0x[0-9a-fA-F]{40}", credentials.funder)):
                raise TradingTransportError("wallet_configuration_invalid")
            backend = _SdkBackend(credentials)
        self._backend = backend
        self._clock = clock
        self.max_book_age_seconds = max_book_age_seconds
        self.future_skew_seconds = future_skew_seconds
        self._preflights: dict[str, dict] = {}
        self._submitted: dict[str, dict] = {}
        self._lock = threading.Lock()

    def close(self) -> None:
        self._backend.close()

    def _geo(self) -> None:
        try:
            data = self._backend.public_json(GEOBLOCK_URL)
        except Exception:
            raise TradingTransportError("geoblock_unknown") from None
        if not isinstance(data, dict) or type(data.get("blocked")) is not bool:
            raise TradingTransportError("geoblock_unknown")
        if data["blocked"]:
            raise TradingTransportError("geoblock_blocked")

    def _fresh(self, timestamp: float) -> None:
        age = self._clock() - timestamp
        if age < -self.future_skew_seconds:
            raise TradingTransportError("book_timestamp_future")
        if age > self.max_book_age_seconds:
            raise TradingTransportError("book_stale")

    def preflight(self, market_id: str, yes_token_id: str) -> dict:
        """No defaults for absent book constraints, negRisk, geography or balances."""
        token = str(yes_token_id)
        with self._lock:
            self._preflights.pop(token, None)
        stage = "market"
        try:
            if not token.isascii() or not token.isdigit() or not 0 < int(token) < 2**256:
                raise TradingTransportError("unsupported_token_protocol")
            self._geo()
            protocol = self._backend.public_json(CLOB_HOST + "/version")
            if not isinstance(protocol, dict) or type(protocol.get("version")) is not int or protocol["version"] != 2:
                raise TradingTransportError("unsupported_exchange_version")
            market = self._backend.public_json(GAMMA_HOST + "/markets/" + urlquote(str(market_id), safe=""))
            if not isinstance(market, dict) or str(market.get("id")) != str(market_id):
                raise TradingTransportError("market_identity_mismatch")
            outcomes = _list(market.get("outcomes"), "market_outcomes_invalid")
            tokens = _list(market.get("clobTokenIds"), "market_tokens_invalid")
            yes = [i for i, value in enumerate(outcomes) if isinstance(value, str) and value.lower() == "yes"]
            if len(tokens) != len(outcomes) or len(yes) != 1 or str(tokens[yes[0]]) != token:
                raise TradingTransportError("yes_token_mismatch")
            if market.get("closed") is not False or market.get("acceptingOrders") is not True:
                raise TradingTransportError("market_not_accepting_orders")
            if market.get("enableOrderBook") is not True:
                raise TradingTransportError("orderbook_disabled_or_unknown")
            condition = market.get("conditionId")
            if not isinstance(condition, str) or not _ORDER_HASH.fullmatch(condition):
                raise TradingTransportError("condition_id_invalid")
            stage = "book"
            book = self._backend.public_json(CLOB_HOST + "/book", {"token_id": token})
            if not isinstance(book, dict) or book.get("asset_id") != token or book.get("market") != condition:
                raise TradingTransportError("book_identity_mismatch")
            neg_risk = book.get("neg_risk")
            if type(neg_risk) is not bool or type(market.get("negRisk")) is not bool or market["negRisk"] != neg_risk:
                raise TradingTransportError("neg_risk_unknown_or_mismatch")
            tick = _decimal(book.get("tick_size"), "tick_size_invalid", positive=True)
            if tick not in _TICKS:
                raise TradingTransportError("tick_size_unsupported")
            minimum = _decimal(book.get("min_order_size"), "minimum_size_invalid", positive=True)
            timestamp = _timestamp(book.get("timestamp"))
            self._fresh(timestamp)
            bids, asks = _levels(book.get("bids")), _levels(book.get("asks"))
            bid = max(Decimal(row["price"]) for row in bids)
            ask = min(Decimal(row["price"]) for row in asks)
            if bid >= ask:
                raise TradingTransportError("book_crossed_or_locked")
            stage = "balance"
            balance = self._backend.balance()
            if not isinstance(balance, dict) or not isinstance(balance.get("allowances"), dict):
                raise TradingTransportError("balance_allowance_invalid")
            balance_usd = _units(balance.get("balance"), "balance_invalid")
            spender = self._backend.exchange(neg_risk).lower()
            allowances = [v for k, v in balance["allowances"].items() if str(k).lower() == spender]
            if len(allowances) != 1:
                raise TradingTransportError("exchange_allowance_missing")
            allowance_usd = _units(allowances[0], "allowance_invalid")
            self._fresh(timestamp)
            quote = dict(asset_id=token, condition_id=condition, best_bid=str(bid), best_ask=str(ask),
                         timestamp=datetime.fromtimestamp(timestamp, timezone.utc).isoformat(),
                         tick_size=str(tick), min_order_size=str(minimum), neg_risk=neg_risk,
                         bids=bids, asks=asks, closed=False, accepting_orders=True)
            result = dict(ok=True, status="ready", quote=quote, balance_usd=str(balance_usd),
                          allowance_usd=str(allowance_usd), checked_at=self._clock(),
                          sdk_package=SDK_PACKAGE, sdk_version=SDK_VERSION)
            with self._lock:
                self._preflights[token] = json.loads(json.dumps(result))
            return result
        except TradingTransportError as exc:
            # Our own errors carry only fixed reason codes, never upstream text.
            reason = str(exc)
        except Exception:
            reason = stage + "_fetch_failed"
        return dict(ok=False, status="blocked", reason=reason)

    def prepare_limit_buy(self, token_id, price, size, neg_risk, tick_size) -> PreparedOrder:
        """Sign only. Raises a sanitized TradingTransportError before any POST."""
        with self._lock:
            preflight = self._preflights.get(str(token_id))
        if preflight is None:
            raise TradingTransportError("successful_preflight_required")
        quote = preflight["quote"]
        timestamp = _timestamp(quote["timestamp"])
        self._fresh(timestamp)
        p = _decimal(price, "price_invalid", positive=True)
        s = _decimal(size, "size_invalid", positive=True)
        tick = _decimal(tick_size, "tick_size_invalid", positive=True)
        if type(neg_risk) is not bool or neg_risk != quote["neg_risk"] or tick != Decimal(quote["tick_size"]):
            raise TradingTransportError("preflight_constraints_changed")
        if p < tick or p > 1 - tick or p % tick:
            raise TradingTransportError("price_not_on_tick")
        if s < Decimal(quote["min_order_size"]):
            raise TradingTransportError("below_minimum_shares")
        if s % Decimal("0.01"):
            raise TradingTransportError("size_precision_invalid")
        amount = p * s
        if amount * _UNIT != (amount * _UNIT).to_integral_value():
            raise TradingTransportError("amount_precision_invalid")
        if amount > Decimal(preflight["balance_usd"]) or amount > Decimal(preflight["allowance_usd"]):
            raise TradingTransportError("insufficient_balance_or_allowance")
        try:
            order_hash, signed_json = self._backend.sign(str(token_id), p, s, neg_risk)
        except Exception:
            raise TradingTransportError("order_signing_failed") from None
        self._fresh(timestamp)
        if not isinstance(order_hash, str) or not _ORDER_HASH.fullmatch(order_hash):
            raise TradingTransportError("order_hash_invalid")
        return PreparedOrder(order_hash, str(token_id), str(p), str(s), str(amount), neg_risk,
                             str(tick), self._clock(), timestamp, signed_json)

    def submit_prepared(self, prepared: PreparedOrder) -> dict:
        """Exactly one POST attempt per hash/instance; caller must enforce DB idempotency."""
        if not isinstance(prepared, PreparedOrder):
            return dict(ok=False, status="blocked", reason="prepared_order_required")
        order_id = prepared.order_hash
        with self._lock:
            existing = self._submitted.get(order_id)
            if existing is not None:
                return dict(existing)
        try:
            self._fresh(prepared.quote_timestamp)
            self._geo()
            self._fresh(prepared.quote_timestamp)
            order = self._backend.restore(prepared)
        except TradingTransportError as exc:
            return dict(ok=False, status="blocked", reason=str(exc), clob_order_id=order_id)
        except Exception:
            return dict(ok=False, status="blocked", reason="prepared_order_invalid", clob_order_id=order_id)
        unknown = dict(ok=False, status="unknown", reason="submit_outcome_unknown", clob_order_id=order_id)
        with self._lock:
            if order_id in self._submitted:
                return dict(self._submitted[order_id])
            self._submitted[order_id] = unknown
        try:
            response = self._backend.post(order)
            result = self._submission_result(response, order_id)
        except Exception:
            # Timeout/5xx/connection loss/malformed response may follow acceptance.
            # No exception, including an HTTP 4xx, proves a definitive order rejection.
            result = unknown
        with self._lock:
            self._submitted[order_id] = result
        return dict(result)

    @staticmethod
    def _submission_result(response, order_id: str) -> dict:
        unknown = dict(ok=False, status="unknown", reason="submit_response_ambiguous", clob_order_id=order_id)
        if not isinstance(response, dict):
            return unknown
        status = response.get("status")
        if response.get("success") is True and response.get("orderID") == order_id and status in {"live", "matched", "delayed", "unmatched"}:
            trade_ids = response.get("tradeIDs", [])
            if not isinstance(trade_ids, list) or not all(isinstance(v, str) for v in trade_ids):
                return unknown
            return dict(ok=True, status="submitted", clob_order_id=order_id, exchange_status=status,
                        trade_ids=trade_ids, reconciliation_required=True)
        # Duplicate acknowledgements require lookup, never release their reservations.
        error = response.get("errorMsg")
        if (response.get("success") is False and isinstance(error, str) and error
                and not response.get("orderID") and not response.get("tradeIDs")
                and not response.get("transactionsHashes") and status in (None, "", "rejected")):
            if "duplicate" not in error.lower() and "already" not in error.lower():
                return dict(ok=False, status="rejected", reason="exchange_rejected", clob_order_id=order_id)
        return unknown

    def get_order(self, order_id: str) -> dict:
        if not isinstance(order_id, str) or not _ORDER_HASH.fullmatch(order_id):
            return dict(ok=False, status="blocked", reason="order_id_invalid")
        try:
            data = self._backend.get_order(order_id)
        except _HttpFailure as exc:
            return dict(ok=False, status="unknown",
                        reason="order_lookup_not_found" if exc.status_code == 404 else "order_query_failed",
                        clob_order_id=order_id, reconciliation_required=True)
        except Exception:
            return dict(ok=False, status="unknown", reason="order_query_failed", clob_order_id=order_id)
        try:
            if not isinstance(data, dict) or data.get("id") != order_id:
                raise TradingTransportError("order_response_invalid")
            original = _decimal(data.get("original_size"), "order_size_invalid", positive=True)
            matched = _decimal(data.get("size_matched"), "order_matched_size_invalid")
            if matched > original or not isinstance(data.get("status"), str):
                raise TradingTransportError("order_response_invalid")
            price = _decimal(data.get("price"), "order_price_invalid", positive=True)
            if price >= 1 or data.get("side") not in ("BUY", "SELL"):
                raise TradingTransportError("order_response_invalid")
            if (not isinstance(data.get("asset_id"), str) or not data["asset_id"].isascii()
                    or not data["asset_id"].isdigit() or not isinstance(data.get("market"), str)
                    or not _ORDER_HASH.fullmatch(data["market"])):
                raise TradingTransportError("order_identity_invalid")
            trades = data.get("associate_trades", [])
            if not isinstance(trades, list) or not all(isinstance(v, str) for v in trades):
                raise TradingTransportError("order_response_invalid")
            state = data["status"].upper()
            canceled = state in {"CANCELED", "CANCELLED"}
            if state not in {"LIVE", "OPEN", "DELAYED", "UNMATCHED", "MATCHED", "FILLED", "CANCELED", "CANCELLED"}:
                raise TradingTransportError("order_status_unknown")
            if state in {"MATCHED", "FILLED"} and matched == 0:
                raise TradingTransportError("order_response_invalid")
            # filled means fully matched at the exchange, NOT settled on chain.
            status = ("filled" if matched == original else "matched") if matched else ("cancelled_unfilled" if canceled else "open")
            return dict(ok=True, status=status, clob_order_id=order_id, exchange_status=data["status"],
                        asset_id=data.get("asset_id"), condition_id=data.get("market"), side=data["side"],
                        price=str(price), trade_ids=trades,
                        original_size=str(original), size_matched=str(matched),
                        remaining_size=str(original - matched), cancellation_confirmed=canceled,
                        settlement_confirmed=False, reconciliation_required=True)
        except TradingTransportError:
            return dict(ok=False, status="unknown", reason="order_response_invalid", clob_order_id=order_id)

    def cancel_order(self, order_id: str) -> dict:
        """One DELETE, no cancel-all; canceled remainder is NOT a reversed fill.

        Cancellation intentionally does not require the new-order geography gate:
        existing exposure must remain cancelable when new orders are disabled.
        """
        if not isinstance(order_id, str) or not _ORDER_HASH.fullmatch(order_id):
            return dict(ok=False, status="blocked", reason="order_id_invalid")
        try:
            data = self._backend.cancel_order(order_id)
        except Exception:
            return dict(ok=False, status="unknown", reason="cancel_outcome_unknown", clob_order_id=order_id)
        if isinstance(data, dict) and isinstance(data.get("canceled"), list) and isinstance(data.get("not_canceled"), dict):
            if order_id in data["canceled"] and order_id not in data["not_canceled"]:
                result = self.get_order(order_id)
                if not result.get("cancellation_confirmed") and result.get("status") != "filled":
                    return dict(ok=False, status="unknown", reason="cancel_reconciliation_pending",
                                clob_order_id=order_id, cancel_acknowledged=True, reconciliation_required=True)
                return {**result, "cancel_acknowledged": True}
            if order_id in data["not_canceled"] and order_id not in data["canceled"]:
                return dict(ok=False, status="not_canceled", reason="exchange_did_not_cancel",
                            clob_order_id=order_id, reconciliation_required=True)
        return dict(ok=False, status="unknown", reason="cancel_response_ambiguous", clob_order_id=order_id)


__all__ = ["ClobTradingTransport", "TradingCredentials", "TradingTransportError", "PreparedOrder",
           "SDK_PACKAGE", "SDK_VERSION"]
