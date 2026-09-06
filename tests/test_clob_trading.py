"""Offline transport tests. No credentials, SDK auth, service, DB or network access."""

from __future__ import annotations

import copy
import json
import threading
import unittest
from dataclasses import replace
from decimal import Decimal
from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace
from unittest.mock import Mock, patch

from weatherbot_v3 import clob_trading as ct


NOW = 1_800_000_000.0
TOKEN = "123456"
CONDITION = "0x" + "a" * 64
ORDER_ID = "0x" + "b" * 64
EXCHANGE = "0x" + "c" * 40
NEG_EXCHANGE = "0x" + "d" * 40
WALLET = "0x" + "e" * 40


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.geo = {"blocked": False, "country": "HK"}
        self.protocol = {"version": 2}
        self.market = dict(id="7", conditionId=CONDITION, outcomes='["No", "Yes"]',
                           clobTokenIds=json.dumps(["987654", TOKEN]), closed=False,
                           acceptingOrders=True, enableOrderBook=True, negRisk=False)
        self.book = dict(asset_id=TOKEN, market=CONDITION, timestamp=str(int(NOW * 1000)),
                         tick_size="0.01", min_order_size="5", neg_risk=False,
                         bids=[dict(price="0.18", size="20"), dict(price="0.19", size="10")],
                         asks=[dict(price="0.22", size="20"), dict(price="0.20", size="10")])
        self.funds = dict(balance="10000000", allowances={EXCHANGE: "5000000", NEG_EXCHANGE: "2000000"})
        self.response = dict(success=True, orderID=ORDER_ID, status="live", tradeIDs=[])
        self.order = dict(id=ORDER_ID, status="LIVE", original_size="5", size_matched="2",
                          price="0.20", side="BUY", asset_id=TOKEN, market=CONDITION,
                          associate_trades=["trade-fixture"])
        self.cancellation = dict(canceled=[ORDER_ID], not_canceled={})
        self.post_exception = None
        self.query_exception = None
        self.cancel_exception = None
        self.geo_exception = None
        self.balance_exception = None

    def public_json(self, url, params=None):
        self.calls.append(("GET", url, params))
        if url == ct.GEOBLOCK_URL:
            if self.geo_exception:
                raise self.geo_exception
            return copy.deepcopy(self.geo)
        if url == ct.CLOB_HOST + "/version":
            return copy.deepcopy(self.protocol)
        if url == ct.GAMMA_HOST + "/markets/7":
            return copy.deepcopy(self.market)
        if url == ct.CLOB_HOST + "/book":
            return copy.deepcopy(self.book)
        raise AssertionError("unexpected public endpoint")

    def exchange(self, neg_risk):
        return NEG_EXCHANGE if neg_risk else EXCHANGE

    def balance(self):
        self.calls.append(("balance",))
        if self.balance_exception:
            raise self.balance_exception
        return copy.deepcopy(self.funds)

    def sign(self, token_id, price, size, neg_risk):
        self.calls.append(("sign", token_id, price, size, neg_risk))
        return ORDER_ID, '{"signature":"synthetic-signed-body"}'

    def restore(self, prepared):
        self.calls.append(("restore",))
        return prepared.signed_json

    def post(self, order):
        self.calls.append(("POST",))
        if self.post_exception:
            raise self.post_exception
        return self.response

    def get_order(self, order_id):
        self.calls.append(("query", order_id))
        if self.query_exception:
            raise self.query_exception
        return self.order

    def cancel_order(self, order_id):
        self.calls.append(("DELETE", order_id))
        if self.cancel_exception:
            raise self.cancel_exception
        return self.cancellation

    def close(self):
        self.calls.append(("close",))


class TransportTests(unittest.TestCase):
    def setUp(self):
        # A regression must fail here, never fall through to a real endpoint.
        self.addCleanup(patch.stopall)
        patch("socket.socket.connect", side_effect=AssertionError("network disabled")).start()
        patch("socket.getaddrinfo", side_effect=AssertionError("network disabled")).start()
        self.backend = FakeBackend()
        self.now = NOW
        self.transport = ct.ClobTradingTransport(backend=self.backend, clock=lambda: self.now)

    def prepare(self):
        result = self.transport.preflight("7", TOKEN)
        self.assertTrue(result["ok"], result)
        return self.transport.prepare_limit_buy(TOKEN, "0.20", "5", False, "0.01")

    def assert_blocked(self, reason):
        result = self.transport.preflight("7", TOKEN)
        self.assertEqual(result, dict(ok=False, status="blocked", reason=reason))
        self.assertNotIn(("POST",), self.backend.calls)

    def test_constructor_does_not_make_requests(self):
        self.assertEqual(self.backend.calls, [])

    def test_named_yes_mapping_and_decimal_funds(self):
        result = self.transport.preflight("7", TOKEN)
        self.assertTrue(result["ok"])
        self.assertEqual(result["quote"]["asset_id"], TOKEN)
        self.assertEqual(result["quote"]["best_bid"], "0.19")
        self.assertEqual(result["quote"]["best_ask"], "0.20")
        self.assertEqual(result["balance_usd"], "10")
        self.assertEqual(result["allowance_usd"], "5")

    def test_geo_blocked_and_unknown_stop_before_account_reads(self):
        for geo, reason in [({"blocked": True}, "geoblock_blocked"), ({}, "geoblock_unknown"),
                            ({"blocked": "false"}, "geoblock_unknown"), ({"blocked": 0}, "geoblock_unknown")]:
            with self.subTest(geo=geo):
                self.backend.calls.clear()
                self.backend.geo = geo
                self.assert_blocked(reason)
                self.assertNotIn(("balance",), self.backend.calls)

    def test_geo_timeout_is_unknown_and_sanitized(self):
        self.backend.geo_exception = TimeoutError("synthetic-sensitive-value")
        self.assert_blocked("geoblock_unknown")

    def test_unsupported_exchange_version_never_guessed(self):
        for protocol in ({}, {"version": "2"}, {"version": 1}, {"version": 3}, "2"):
            with self.subTest(protocol=protocol):
                self.backend.protocol = protocol
                self.assert_blocked("unsupported_exchange_version")

    def test_unsupported_token_protocol(self):
        for token in ("0x123", "", "-1", str(2**256), "0"):
            self.assertEqual(self.transport.preflight("7", token)["reason"], "unsupported_token_protocol")

    def test_wrong_outcome_never_falls_back(self):
        self.backend.market["clobTokenIds"] = json.dumps([TOKEN, "987654"])
        self.assert_blocked("yes_token_mismatch")

    def test_closed_or_unknown_market_fails(self):
        for field, value, reason in (("closed", True, "market_not_accepting_orders"),
                                     ("closed", None, "market_not_accepting_orders"),
                                     ("acceptingOrders", None, "market_not_accepting_orders"),
                                     ("enableOrderBook", None, "orderbook_disabled_or_unknown")):
            with self.subTest(field=field):
                self.backend.market = FakeBackend().market
                self.backend.market[field] = value
                self.assert_blocked(reason)

    def test_book_identity_is_exact(self):
        for field in ("asset_id", "market"):
            with self.subTest(field=field):
                self.backend.book = FakeBackend().book
                self.backend.book[field] = "wrong"
                self.assert_blocked("book_identity_mismatch")

    def test_neg_risk_cannot_be_defaulted_or_coerced(self):
        for value in (None, "false", 0, True):
            with self.subTest(value=value):
                self.backend.book["neg_risk"] = value
                self.assert_blocked("neg_risk_unknown_or_mismatch")

    def test_neg_risk_uses_matching_exchange_allowance(self):
        self.backend.market["negRisk"] = self.backend.book["neg_risk"] = True
        result = self.transport.preflight("7", TOKEN)
        self.assertEqual(result["allowance_usd"], "2")

    def test_missing_stale_invalid_and_future_timestamps(self):
        for value, reason in ((None, "book_timestamp_invalid"), ("bad", "book_timestamp_invalid"),
                              ("2026-01-01T00:00:00", "book_timestamp_invalid"),
                              (str(int((NOW - 31) * 1000)), "book_stale"),
                              (str(int((NOW + 3) * 1000)), "book_timestamp_future")):
            with self.subTest(value=value):
                self.backend.book["timestamp"] = value
                self.assert_blocked(reason)

    def test_constraints_do_not_default(self):
        for field, value, reason in (("tick_size", None, "tick_size_invalid"),
                                     ("tick_size", "0.02", "tick_size_unsupported"),
                                     ("min_order_size", None, "minimum_size_invalid"),
                                     ("min_order_size", "NaN", "minimum_size_invalid")):
            with self.subTest(field=field, value=value):
                self.backend.book = FakeBackend().book
                self.backend.book[field] = value
                self.assert_blocked(reason)

    def test_all_official_current_tick_sizes_supported(self):
        for tick in ("0.1", "0.01", "0.005", "0.0025", "0.001", "0.0001"):
            with self.subTest(tick=tick):
                self.backend.book["tick_size"] = tick
                self.assertTrue(self.transport.preflight("7", TOKEN)["ok"])
                order = self.transport.prepare_limit_buy(TOKEN, "0.2", "5", False, tick)
                self.assertEqual(Decimal(order.amount), Decimal("1"))

    def test_invalid_or_absent_depth_blocks(self):
        for levels, reason in (([], "book_side_absent"), ([dict(price="NaN", size="5")], "book_price_invalid"),
                               ([dict(price="0.2", size="-1")], "book_size_invalid"),
                               ([{}], "book_price_invalid")):
            with self.subTest(levels=levels):
                self.backend.book["bids"] = levels
                self.assert_blocked(reason)

    def test_crossed_book_blocks(self):
        self.backend.book["bids"] = [dict(price="0.3", size="5")]
        self.assert_blocked("book_crossed_or_locked")

    def test_balance_failure_is_not_zero(self):
        self.backend.balance_exception = RuntimeError("synthetic-secret")
        self.assert_blocked("balance_fetch_failed")

    def test_allowance_is_spender_specific_and_base_units_are_integral(self):
        self.backend.funds["allowances"] = {NEG_EXCHANGE: "90000000"}
        self.assert_blocked("exchange_allowance_missing")
        self.backend.funds = FakeBackend().funds
        self.backend.funds["balance"] = "1.5"
        self.assert_blocked("balance_invalid")

    def test_preparation_is_sign_only_with_safe_repr(self):
        order = self.prepare()
        self.assertNotIn(("POST",), self.backend.calls)
        self.assertEqual(order.order_hash, ORDER_ID)
        self.assertEqual(order.order_id, ORDER_ID)
        self.assertNotIn("synthetic-signed-body", repr(order))
        self.assertNotIn("signed_json", order.public_dict())
        self.assertIn(("sign", TOKEN, Decimal("0.20"), Decimal("5"), False), self.backend.calls)

    def test_prepare_requires_successful_current_preflight(self):
        with self.assertRaisesRegex(ct.TradingTransportError, "successful_preflight_required"):
            self.transport.prepare_limit_buy(TOKEN, "0.20", "5", False, "0.01")
        self.prepare()
        self.backend.geo = {"blocked": True}
        self.transport.preflight("7", TOKEN)
        with self.assertRaisesRegex(ct.TradingTransportError, "successful_preflight_required"):
            self.transport.prepare_limit_buy(TOKEN, "0.20", "5", False, "0.01")

    def test_external_preflight_mutation_cannot_relax_checks(self):
        result = self.transport.preflight("7", TOKEN)
        result["quote"]["min_order_size"] = "0.01"
        with self.assertRaisesRegex(ct.TradingTransportError, "below_minimum_shares"):
            self.transport.prepare_limit_buy(TOKEN, "0.2", "1", False, "0.01")

    def test_prepare_rejects_precision_tick_and_limits(self):
        self.transport.preflight("7", TOKEN)
        cases = [("0.201", "5", False, "0.01", "price_not_on_tick"),
                 ("0.20", "4", False, "0.01", "below_minimum_shares"),
                 ("0.20", "5.001", False, "0.01", "size_precision_invalid"),
                 ("0.20", "30", False, "0.01", "insufficient_balance_or_allowance"),
                 ("NaN", "5", False, "0.01", "price_invalid"),
                 ("0.20", "Infinity", False, "0.01", "size_invalid"),
                 ("0.20", "5", True, "0.01", "preflight_constraints_changed"),
                 ("0.20", "5", False, "0.005", "preflight_constraints_changed")]
        for price, size, neg, tick, reason in cases:
            with self.subTest(reason=reason), self.assertRaisesRegex(ct.TradingTransportError, reason):
                self.transport.prepare_limit_buy(TOKEN, price, size, neg, tick)

    def test_quote_expiry_before_sign_and_submit(self):
        order = self.prepare()
        self.now += 31
        with self.assertRaisesRegex(ct.TradingTransportError, "book_stale"):
            self.transport.prepare_limit_buy(TOKEN, "0.20", "5", False, "0.01")
        self.assertEqual(self.transport.submit_prepared(order)["reason"], "book_stale")
        self.assertNotIn(("POST",), self.backend.calls)

    def test_submission_rechecks_geo(self):
        order = self.prepare()
        self.backend.geo = {"blocked": True}
        self.assertEqual(self.transport.submit_prepared(order)["reason"], "geoblock_blocked")
        self.assertNotIn(("POST",), self.backend.calls)

    def test_accepted_states_preserve_exchange_status_and_no_retry(self):
        for state in ("live", "matched", "delayed", "unmatched"):
            with self.subTest(state=state):
                transport = ct.ClobTradingTransport(backend=self.backend, clock=lambda: self.now)
                transport.preflight("7", TOKEN)
                order = transport.prepare_limit_buy(TOKEN, "0.2", "5", False, "0.01")
                self.backend.response["status"] = state
                result = transport.submit_prepared(order)
                self.assertEqual(result["status"], "submitted")
                self.assertEqual(result["exchange_status"], state)
                count = self.backend.calls.count(("POST",))
                self.assertEqual(transport.submit_prepared(order), result)
                self.assertEqual(self.backend.calls.count(("POST",)), count)

    def test_timeout_5xx_4xx_and_unexpected_errors_are_unknown_once(self):
        for exc in (TimeoutError("synthetic-secret"), ct._HttpFailure(503), ct._HttpFailure(400),
                    ConnectionError("synthetic-secret"), ValueError("synthetic-secret")):
            with self.subTest(error=type(exc).__name__):
                self.transport = ct.ClobTradingTransport(backend=self.backend, clock=lambda: self.now)
                order = self.prepare()
                self.backend.post_exception = exc
                before = self.backend.calls.count(("POST",))
                result = self.transport.submit_prepared(order)
                self.assertEqual(result["status"], "unknown")
                self.assertNotIn("synthetic-secret", repr(result))
                self.transport.submit_prepared(order)
                self.assertEqual(self.backend.calls.count(("POST",)), before + 1)

    def test_explicit_rejection_does_not_echo_body(self):
        order = self.prepare()
        self.backend.response = dict(success=False, errorMsg="invalid order synthetic-secret")
        result = self.transport.submit_prepared(order)
        self.assertEqual(result["status"], "rejected")
        self.assertNotIn("synthetic-secret", repr(result))

    def test_ambiguous_duplicate_malformed_responses_are_unknown(self):
        for response in (None, "bad", {}, {"success": True},
                         dict(success=True, orderID=CONDITION, status="live"),
                         dict(success=True, orderID=ORDER_ID, status=[]),
                         dict(success=False, errorMsg="duplicate order"),
                         dict(success=False, errorMsg="already matched"),
                         dict(success=False, errorMsg="bad", status="matched"),
                         dict(success=False, errorMsg="bad", tradeIDs=["trade"])):
            with self.subTest(response=response):
                self.transport = ct.ClobTradingTransport(backend=self.backend, clock=lambda: self.now)
                order = self.prepare()
                self.backend.response = response
                self.assertEqual(self.transport.submit_prepared(order)["status"], "unknown")

    def test_concurrent_submit_claim_is_single_attempt(self):
        order = self.prepare()
        entered, release = threading.Event(), threading.Event()
        def post(_):
            self.backend.calls.append(("POST",))
            entered.set()
            if not release.wait(3):
                raise TimeoutError
            return self.backend.response
        self.backend.post = post
        results = []
        thread = threading.Thread(target=lambda: results.append(self.transport.submit_prepared(order)))
        thread.start()
        try:
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.transport.submit_prepared(order)["status"], "unknown")
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.backend.calls.count(("POST",)), 1)
        self.assertEqual(results[0]["status"], "submitted")

    def test_query_preserves_partial_fill_and_not_found_uncertainty(self):
        result = self.transport.get_order(ORDER_ID)
        self.assertEqual(result["size_matched"], "2")
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["remaining_size"], "3")
        self.assertEqual(result["trade_ids"], ["trade-fixture"])
        self.backend.query_exception = ct._HttpFailure(404)
        result = self.transport.get_order(ORDER_ID)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "order_lookup_not_found")
        self.assertTrue(result["reconciliation_required"])

    def test_query_failure_and_malformed_data_are_unknown(self):
        self.backend.query_exception = TimeoutError("synthetic-secret")
        result = self.transport.get_order(ORDER_ID)
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("synthetic-secret", repr(result))
        self.backend.query_exception = None
        self.backend.order["size_matched"] = "6"
        self.assertEqual(self.transport.get_order(ORDER_ID)["status"], "unknown")

    def test_cancel_is_available_when_new_orders_blocked(self):
        self.backend.geo = {"blocked": True}
        self.backend.order.update(status="CANCELED", size_matched="0")
        result = self.transport.cancel_order(ORDER_ID)
        self.assertEqual(result["status"], "cancelled_unfilled")
        self.assertTrue(result["reconciliation_required"])
        self.assertEqual(self.backend.calls, [("DELETE", ORDER_ID), ("query", ORDER_ID)])

    def test_get_order_normalizes_exchange_lifecycle(self):
        for exchange, matched, expected in (("LIVE", "0", "open"), ("DELAYED", "0", "open"),
                                            ("LIVE", "2", "matched"), ("MATCHED", "5", "filled"),
                                            ("CANCELED", "0", "cancelled_unfilled"),
                                            ("CANCELED", "2", "matched"), ("CANCELED", "5", "filled"),
                                            ("NEW_STATUS", "0", "unknown"), ("MATCHED", "0", "unknown")):
            with self.subTest(exchange=exchange, matched=matched):
                self.backend.order.update(status=exchange, size_matched=matched)
                result = self.transport.get_order(ORDER_ID)
                self.assertEqual(result["status"], expected)
                self.assertFalse(result.get("settlement_confirmed", False))

    def test_canceled_ack_with_partial_fill_keeps_exposure(self):
        self.backend.order.update(status="CANCELED", size_matched="2")
        result = self.transport.cancel_order(ORDER_ID)
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["size_matched"], "2")
        self.assertTrue(result["cancel_acknowledged"])

    def test_canceled_ack_without_reconciliation_stays_unknown(self):
        self.backend.query_exception = TimeoutError()
        result = self.transport.cancel_order(ORDER_ID)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason"], "cancel_reconciliation_pending")
        self.backend.query_exception = None
        self.backend.order.update(status="LIVE", size_matched="0")
        self.assertEqual(self.transport.cancel_order(ORDER_ID)["status"], "unknown")

    def test_cancel_requires_explicit_id_ack(self):
        for response, expected in ((dict(canceled=[], not_canceled={ORDER_ID: "already matched synthetic-secret"}), "not_canceled"),
                                    (dict(canceled=[], not_canceled={}), "unknown"),
                                    (dict(canceled=[ORDER_ID], not_canceled={ORDER_ID: "conflict"}), "unknown"),
                                    ({}, "unknown")):
            self.backend.cancellation = response
            result = self.transport.cancel_order(ORDER_ID)
            self.assertEqual(result["status"], expected)
            self.assertNotIn("synthetic-secret", repr(result))

    def test_cancel_timeout_never_retries_or_claims_canceled(self):
        self.backend.cancel_exception = TimeoutError("synthetic-secret")
        self.assertEqual(self.transport.cancel_order(ORDER_ID)["status"], "unknown")
        self.assertEqual(self.backend.calls, [("DELETE", ORDER_ID)])

    def test_invalid_query_and_cancel_ids_do_not_reach_backend(self):
        for order_id in (None, "", "../../orders", "not-a-hash"):
            self.assertEqual(self.transport.get_order(order_id)["status"], "blocked")
            self.assertEqual(self.transport.cancel_order(order_id)["status"], "blocked")
        self.assertEqual(self.backend.calls, [])

    def test_sensitive_credentials_are_not_in_repr_or_validation_errors(self):
        creds = ct.TradingCredentials("synthetic-key", "synthetic-api", "synthetic-secret", "synthetic-pass", WALLET, 3)
        self.assertNotIn("synthetic", repr(creds))
        with patch.object(ct, "_SdkBackend") as factory:
            ct.ClobTradingTransport(creds)
            factory.assert_called_once_with(creds)
        with self.assertRaisesRegex(ct.TradingTransportError, "credentials_incomplete"):
            ct.ClobTradingTransport(replace(creds, api_key=""))

    def test_freshness_configuration_is_finite(self):
        for value in (float("nan"), float("inf"), 0, -1):
            with self.assertRaisesRegex(ct.TradingTransportError, "freshness_policy_invalid"):
                ct.ClobTradingTransport(backend=self.backend, max_book_age_seconds=value)

    def test_http_session_no_redirect_retry_or_error_body(self):
        for status in (302, 400, 500):
            session = Mock()
            session.request.return_value = SimpleNamespace(status_code=status)
            with patch.object(ct.requests, "Session", return_value=session):
                http = ct._JsonSession()
                with self.assertRaises(ct._HttpFailure):
                    http.request("POST", ct.CLOB_HOST + "/order", data="synthetic-signed-body")
                self.assertFalse(session.trust_env)
                self.assertEqual(session.request.call_count, 1)
                self.assertFalse(session.request.call_args.kwargs["allow_redirects"])
                self.assertEqual(session.request.call_args.kwargs["timeout"], (5, 10))
                self.assertEqual(session.mount.call_args.args[1].max_retries.total, 0)

    def test_http_failure_sanitizes_network_exception(self):
        session = Mock()
        session.request.side_effect = ct.requests.Timeout("synthetic-secret")
        with patch.object(ct.requests, "Session", return_value=session):
            http = ct._JsonSession()
            with self.assertRaisesRegex(ct.TradingTransportError, "^network_request_failed$"):
                http.request("POST", ct.CLOB_HOST + "/order")
            self.assertEqual(session.request.call_count, 1)

    def test_sdk_version_is_pinned_before_loading_credentials(self):
        creds = ct.TradingCredentials("fixture", "fixture", "fixture", "fixture", WALLET, 3)
        with patch.object(ct, "version", return_value="1.0.2"):
            with self.assertRaisesRegex(ct.TradingTransportError, "live_sdk_version_mismatch"):
                ct._SdkBackend(creds)


class SdkContractTests(unittest.TestCase):
    """Run with requirements-live installed, using ephemeral keys and mocked HTTP."""

    def setUp(self):
        try:
            installed = ct.version(ct.SDK_PACKAGE)
        except PackageNotFoundError:
            self.skipTest("optional live SDK not installed")
        if installed != ct.SDK_VERSION:
            self.skipTest("requires pinned optional live SDK")
        self.addCleanup(patch.stopall)
        patch("socket.socket.connect", side_effect=AssertionError("network disabled")).start()
        patch("socket.getaddrinfo", side_effect=AssertionError("network disabled")).start()
        self.backends = []
        self.addCleanup(lambda: [backend.close() for backend in self.backends])

    def backend(self, signature_type=0):
        import base64
        from eth_account import Account
        account = Account.create()
        credentials = ct.TradingCredentials(
            private_key=account.key.hex(), api_key="00000000-0000-0000-0000-000000000001",
            api_secret=base64.urlsafe_b64encode(b"offline-test-credentials-only").decode(),
            api_passphrase="offline-test-passphrase-only",
            funder=account.address if signature_type == 0 else WALLET,
            signature_type=signature_type,
        )
        backend = ct._SdkBackend(credentials)
        self.backends.append(backend)
        backend.http.session.request = Mock(side_effect=AssertionError("HTTP must be explicitly mocked"))
        return backend, account

    def signed(self, backend, neg_risk=False):
        order_hash, signed_json = backend.sign(TOKEN, Decimal("0.2025"), Decimal("5"), neg_risk)
        return ct.PreparedOrder(order_hash, TOKEN, "0.2025", "5", "1.0125", neg_risk,
                                "0.0025", NOW, NOW, signed_json)

    def test_official_sign_hash_restore_and_signature_contract(self):
        from eth_account import Account
        from eth_account.messages import encode_typed_data
        for signature_type in (0, 1, 2, 3):
            for neg_risk in (False, True):
                with self.subTest(signature_type=signature_type, neg_risk=neg_risk):
                    backend, account = self.backend(signature_type)
                    prepared = self.signed(backend, neg_risk)
                    order = backend.restore(prepared)
                    self.assertEqual(int(order.makerAmount), 1_012_500)
                    self.assertEqual(int(order.takerAmount), 5_000_000)
                    self.assertEqual(int(order.side), 0)
                    self.assertEqual(int(order.expiration), 0)
                    self.assertEqual(int(order.signatureType), signature_type)
                    self.assertEqual(order.maker.lower(), backend.client.builder.funder.lower())
                    builder = backend._builder(neg_risk)
                    typed_data = builder.build_order_typed_data(order)
                    self.assertEqual(typed_data["domain"]["verifyingContract"], backend.exchange(neg_risk))
                    self.assertEqual(builder.build_order_hash(typed_data), prepared.order_hash)
                    if signature_type != 3:
                        recovered = Account.recover_message(encode_typed_data(full_message=typed_data), signature=order.signature)
                        self.assertEqual(recovered.lower(), account.address.lower())
                    else:
                        self.assertEqual(order.signer.lower(), WALLET.lower())
                        self.assertGreater(len(order.signature), 132)
                    backend.http.session.request.assert_not_called()

    def test_restore_rejects_changed_signed_domain_or_amount(self):
        backend, _ = self.backend()
        prepared = self.signed(backend)
        for tampered in (replace(prepared, neg_risk=True), replace(prepared, amount="2"),
                         replace(prepared, token_id="9"), replace(prepared, size="6")):
            with self.assertRaises(ct.TradingTransportError):
                backend.restore(tampered)
        backend.http.session.request.assert_not_called()

    def test_official_post_serialization_and_l2_headers_single_request(self):
        backend, _ = self.backend()
        prepared = self.signed(backend)
        backend.http.session.request = Mock(return_value=SimpleNamespace(status_code=200, json=lambda: {
            "success": True, "orderID": prepared.order_hash, "status": "matched", "tradeIDs": ["offline-trade"]}))
        result = backend.post(backend.restore(prepared))
        self.assertTrue(result["success"])
        self.assertFalse(backend.client.retry_on_error)
        backend.http.session.request.assert_called_once()
        call = backend.http.session.request.call_args
        self.assertEqual(call.args, ("POST", ct.CLOB_HOST + "/order"))
        payload = json.loads(call.kwargs["data"])
        self.assertEqual(payload["orderType"], "GTC")
        self.assertEqual(payload["order"]["side"], "BUY")
        self.assertEqual(payload["order"]["makerAmount"], "1012500")
        self.assertEqual(payload["order"]["takerAmount"], "5000000")
        self.assertFalse(payload["deferExec"])
        self.assertFalse(payload["postOnly"])
        self.assertEqual(call.kwargs["headers"]["Content-Type"], "application/json")
        self.assertTrue({"POLY_ADDRESS", "POLY_API_KEY", "POLY_PASSPHRASE", "POLY_SIGNATURE", "POLY_TIMESTAMP"}.issubset(call.kwargs["headers"]))

    def test_official_post_timeout_is_unknown_without_second_http_request(self):
        backend, _ = self.backend()
        prepared = self.signed(backend)
        backend.http.session.request = Mock(side_effect=ct.requests.Timeout("offline-timeout"))
        backend.public_json = Mock(return_value={"blocked": False})
        transport = ct.ClobTradingTransport(backend=backend, clock=lambda: NOW)
        self.assertEqual(transport.submit_prepared(prepared)["status"], "unknown")
        self.assertEqual(transport.submit_prepared(prepared)["status"], "unknown")
        backend.http.session.request.assert_called_once()

    def test_official_balance_query_and_cancel_contracts(self):
        backend, _ = self.backend(3)
        backend.http.session.request = Mock(return_value=SimpleNamespace(status_code=200, json=lambda: {}))
        backend.balance()
        call = backend.http.session.request.call_args
        self.assertEqual(call.args, ("GET", ct.CLOB_HOST + "/balance-allowance"))
        self.assertEqual(call.kwargs["params"], {"signature_type": 3, "asset_type": "COLLATERAL"})
        backend.get_order(ORDER_ID)
        self.assertEqual(backend.http.session.request.call_args.args, ("GET", ct.CLOB_HOST + "/data/order/" + ORDER_ID))
        backend.cancel_order(ORDER_ID)
        call = backend.http.session.request.call_args
        self.assertEqual(call.args, ("DELETE", ct.CLOB_HOST + "/order"))
        self.assertEqual(json.loads(call.kwargs["data"]), {"orderID": ORDER_ID})
        self.assertEqual(backend.http.session.request.call_count, 3)


if __name__ == "__main__":
    unittest.main()
