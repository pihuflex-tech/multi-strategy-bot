"""
test_data_validator.py
Comprehensive offline unit tests for FeedDataValidator.
Validates required top-level fields, spot price sanity, non-finite values,
zero/negative prices, option strikes/types, missing expiry, empty collections,
extra optional fields, and large option chains.
"""

import copy
import math
import unittest

from src.feed.data_validator import FeedDataValidator


class TestFeedDataValidator(unittest.TestCase):
    """Offline unit test suite for FeedDataValidator."""

    def setUp(self):
        self.validator = FeedDataValidator(require_expiry=True, allow_empty_options=True)
        self.base_valid_payload = {
            "timestamp": "2026-10-25 09:20:00.000",
            "server_status": "RUNNING",
            "spot": {"symbol": "NIFTY 50", "ltp": 24850.0, "high": 24890.0, "low": 24810.0, "net_change": 40.0},
            "futures": {"symbol": "NIFTY_FUT_OCT", "ltp": 24880.0, "expiry": "2026-10-29", "bid": 24879.0, "ask": 24881.0},
            "indices": {"india_vix": {"ltp": 13.5}},
            "options": {
                "current_expiry": {
                    "24850_CE": {
                        "strike": 24850.0, "option_type": "CE", "expiry": "2026-10-29",
                        "ltp": 120.0, "bid": 119.5, "ask": 120.5, "volume": 10000,
                        "open_interest": 50000, "greeks": {"delta": 0.50, "iv": 14.0}
                    },
                    "24850_PE": {
                        "strike": 24850.0, "option_type": "PE", "expiry": "2026-10-29",
                        "ltp": 115.0, "bid": 114.5, "ask": 115.5, "volume": 9000,
                        "open_interest": 45000, "greeks": {"delta": -0.50, "iv": 14.5}
                    }
                }
            }
        }

    def test_01_valid_payload_passes(self):
        """Valid full payload passes validation cleanly."""
        valid, err = self.validator.validate(self.base_valid_payload)
        self.assertTrue(valid)
        self.assertIsNone(err)
        self.assertTrue(self.validator.is_valid(self.base_valid_payload))

    def test_02_flat_options_structure_passes(self):
        """Flat options dictionary structure is supported and passes."""
        flat_payload = copy.deepcopy(self.base_valid_payload)
        flat_payload["options"] = {
            "24850_CE": {
                "strike": 24850.0, "option_type": "CE", "expiry": "2026-10-29",
                "ltp": 120.0, "bid": 119.5, "ask": 120.5
            }
        }
        valid, err = self.validator.validate(flat_payload)
        self.assertTrue(valid)
        self.assertIsNone(err)

    def test_03_missing_required_top_level_fields(self):
        """7. Missing required fields: timestamp, spot, or options rejected."""
        # Missing timestamp
        p1 = copy.deepcopy(self.base_valid_payload)
        del p1["timestamp"]
        v1, e1 = self.validator.validate(p1)
        self.assertFalse(v1)
        self.assertIn("MISSING_FIELD: timestamp", e1)

        # Missing spot
        p2 = copy.deepcopy(self.base_valid_payload)
        del p2["spot"]
        v2, e2 = self.validator.validate(p2)
        self.assertFalse(v2)
        self.assertIn("MISSING_FIELD: spot", e2)

        # Missing options
        p3 = copy.deepcopy(self.base_valid_payload)
        del p3["options"]
        v3, e3 = self.validator.validate(p3)
        self.assertFalse(v3)
        self.assertIn("MISSING_FIELD: options", e3)

        # Missing spot.ltp
        p4 = copy.deepcopy(self.base_valid_payload)
        del p4["spot"]["ltp"]
        v4, e4 = self.validator.validate(p4)
        self.assertFalse(v4)
        self.assertIn("MISSING_FIELD: spot.ltp", e4)

    def test_04_incorrect_nested_object_types(self):
        """8. Incorrect nested object types: non-dict payload, spot, or options rejected."""
        # Root is list
        v1, e1 = self.validator.validate(["not_a_dict"])
        self.assertFalse(v1)
        self.assertIn("INVALID_PAYLOAD_TYPE", e1)

        # Spot is not dict
        p2 = copy.deepcopy(self.base_valid_payload)
        p2["spot"] = 24850.0
        v2, e2 = self.validator.validate(p2)
        self.assertFalse(v2)
        self.assertIn("INVALID_TYPE: spot must be dict", e2)

        # Options is not dict
        p3 = copy.deepcopy(self.base_valid_payload)
        p3["options"] = ["24850_CE", "24850_PE"]
        v3, e3 = self.validator.validate(p3)
        self.assertFalse(v3)
        self.assertIn("INVALID_TYPE: options must be dict", e3)

        # Timestamp is empty string or number
        p4 = copy.deepcopy(self.base_valid_payload)
        p4["timestamp"] = ""
        v4, e4 = self.validator.validate(p4)
        self.assertFalse(v4)
        self.assertIn("INVALID_TYPE: timestamp must be non-empty string", e4)

        # Contract entry in bucket is not dict
        p5 = copy.deepcopy(self.base_valid_payload)
        p5["options"]["current_expiry"]["24850_CE"] = 120.0
        v5, e5 = self.validator.validate(p5)
        self.assertFalse(v5)
        self.assertIn("INVALID_TYPE", e5)

    def test_05_nan_and_positive_negative_infinity(self):
        """9. NaN and positive/negative Infinity rejected across all numeric fields."""
        for bad_val in [float("nan"), float("inf"), float("-inf")]:
            # Spot LTP non-finite
            p1 = copy.deepcopy(self.base_valid_payload)
            p1["spot"]["ltp"] = bad_val
            v1, e1 = self.validator.validate(p1)
            self.assertFalse(v1)
            self.assertIn("INVALID_PRICE: spot.ltp must be a finite number", e1)

            # Spot high non-finite
            p2 = copy.deepcopy(self.base_valid_payload)
            p2["spot"]["high"] = bad_val
            v2, e2 = self.validator.validate(p2)
            self.assertFalse(v2)
            self.assertIn("NON_FINITE_VALUE", e2)

            # Futures LTP non-finite
            p3 = copy.deepcopy(self.base_valid_payload)
            p3["futures"]["ltp"] = bad_val
            v3, e3 = self.validator.validate(p3)
            self.assertFalse(v3)
            self.assertIn("INVALID_PRICE", e3)

            # Option contract LTP non-finite
            p4 = copy.deepcopy(self.base_valid_payload)
            p4["options"]["current_expiry"]["24850_CE"]["ltp"] = bad_val
            v4, e4 = self.validator.validate(p4)
            self.assertFalse(v4)
            self.assertIn("INVALID_PRICE", e4)

            # Greek delta non-finite
            p5 = copy.deepcopy(self.base_valid_payload)
            p5["options"]["current_expiry"]["24850_CE"]["greeks"]["delta"] = bad_val
            v5, e5 = self.validator.validate(p5)
            self.assertFalse(v5)
            self.assertIn("NON_FINITE_VALUE", e5)

    def test_06_zero_or_negative_prices_where_invalid(self):
        """10. Zero or negative prices where invalid: spot <= 0 and option ltp <= 0 rejected."""
        # Spot LTP is 0.0
        p1 = copy.deepcopy(self.base_valid_payload)
        p1["spot"]["ltp"] = 0.0
        v1, e1 = self.validator.validate(p1)
        self.assertFalse(v1)
        self.assertIn("INVALID_PRICE: spot.ltp must be positive", e1)

        # Spot LTP is negative
        p2 = copy.deepcopy(self.base_valid_payload)
        p2["spot"]["ltp"] = -24850.0
        v2, e2 = self.validator.validate(p2)
        self.assertFalse(v2)
        self.assertIn("INVALID_PRICE: spot.ltp must be positive", e2)

        # Option LTP is 0.0
        p3 = copy.deepcopy(self.base_valid_payload)
        p3["options"]["current_expiry"]["24850_CE"]["ltp"] = 0.0
        v3, e3 = self.validator.validate(p3)
        self.assertFalse(v3)
        self.assertIn("INVALID_PRICE", e3)

        # Option LTP is negative
        p4 = copy.deepcopy(self.base_valid_payload)
        p4["options"]["current_expiry"]["24850_CE"]["ltp"] = -10.0
        v4, e4 = self.validator.validate(p4)
        self.assertFalse(v4)
        self.assertIn("INVALID_PRICE", e4)

        # Futures LTP is 0.0
        p5 = copy.deepcopy(self.base_valid_payload)
        p5["futures"]["ltp"] = 0.0
        v5, e5 = self.validator.validate(p5)
        self.assertFalse(v5)
        self.assertIn("INVALID_PRICE: futures.ltp must be positive", e5)

    def test_07_invalid_option_types(self):
        """11. Invalid option types: rejected if not CE or PE."""
        for bad_type in ["CALL", "PUT", "XX", "", 123, None]:
            p = copy.deepcopy(self.base_valid_payload)
            p["options"]["current_expiry"]["24850_CE"]["option_type"] = bad_type
            v, e = self.validator.validate(p)
            self.assertFalse(v)
            self.assertTrue("INVALID_OPTION_TYPE" in e or "MISSING_FIELD" in e)

    def test_08_invalid_strikes(self):
        """12. Invalid strikes: negative, zero, non-finite, or non-numeric rejected."""
        for bad_strike in [0, -24800, float("nan"), float("inf"), "ATM", None, True]:
            p = copy.deepcopy(self.base_valid_payload)
            p["options"]["current_expiry"]["24850_CE"]["strike"] = bad_strike
            v, e = self.validator.validate(p)
            self.assertFalse(v)
            self.assertTrue("INVALID_STRIKE" in e or "MISSING_FIELD" in e)

    def test_09_missing_expiry_where_required(self):
        """13. Missing expiry where required: fails when neither contract nor parent specifies expiry."""
        p = copy.deepcopy(self.base_valid_payload)
        del p["options"]["current_expiry"]["24850_CE"]["expiry"]
        # Bucket key "current_expiry" does not contain a date string
        v, e = self.validator.validate(p)
        self.assertFalse(v)
        self.assertIn("MISSING_EXPIRY", e)

        # But if top-level has current_expiry date, it satisfies requirement
        p["current_expiry"] = "2026-10-29"
        v_ok, e_ok = self.validator.validate(p)
        self.assertTrue(v_ok)

    def test_10_empty_option_collections(self):
        """14. Empty option collections: accepted when allow_empty_options=True, rejected when False."""
        empty_payload = copy.deepcopy(self.base_valid_payload)
        empty_payload["options"] = {}

        # Default: allowed (valid collection shape)
        val_default = FeedDataValidator(allow_empty_options=True)
        v_def, e_def = val_default.validate(empty_payload)
        self.assertTrue(v_def)

        # Strict: rejected
        val_strict = FeedDataValidator(allow_empty_options=False)
        v_str, e_str = val_strict.validate(empty_payload)
        self.assertFalse(v_str)
        self.assertIn("EMPTY_OPTIONS_COLLECTION", e_str)

    def test_11_extra_optional_fields_remain_backward_compatible(self):
        """15. Extra optional fields: backward-compatible and allowed."""
        extra_payload = copy.deepcopy(self.base_valid_payload)
        extra_payload["heavyweights"] = {
            "RELIANCE-EQ": {"ltp": 2900.0, "volume": 100000},
            "HDFCBANK-EQ": {"ltp": 1500.0, "volume": 150000}
        }
        extra_payload["custom_telemetry"] = {"source": "angel_one", "ping_ms": 12}
        extra_payload["options"]["current_expiry"]["24850_CE"]["custom_flag"] = "HOT"

        valid, err = self.validator.validate(extra_payload)
        self.assertTrue(valid)
        self.assertIsNone(err)

    def test_12_large_option_chain_validated(self):
        """16. Large valid option chain: validates 100 contracts swiftly."""
        large_payload = {
            "timestamp": "2026-10-25 09:20:00.000",
            "spot": {"symbol": "NIFTY 50", "ltp": 24800.0},
            "options": {"current_expiry": {}}
        }
        for strike in range(22500, 27500, 50):
            large_payload["options"]["current_expiry"][f"{strike}_CE"] = {
                "strike": strike, "option_type": "CE", "expiry": "2026-10-29",
                "ltp": 100.0, "bid": 99.5, "ask": 100.5, "volume": 5000,
                "open_interest": 20000, "greeks": {"delta": 0.5, "iv": 14.0}
            }
        valid, err = self.validator.validate(large_payload)
        self.assertTrue(valid)
        self.assertIsNone(err)

    def test_13_bid_ask_spread_relationships(self):
        """Bid/ask relationship: normal (bid < ask), equal (bid == ask), crossed (bid > ask rejected), missing bid or ask."""
        # 1. Normal bid < ask in futures and options passes (base payload already has this)
        v_base, e_base = self.validator.validate(self.base_valid_payload)
        self.assertTrue(v_base)

        # 2. Equal bid == ask passes for futures
        p_eq = copy.deepcopy(self.base_valid_payload)
        p_eq["futures"]["bid"] = 24880.0
        p_eq["futures"]["ask"] = 24880.0
        v_eq, e_eq = self.validator.validate(p_eq)
        self.assertTrue(v_eq, f"Equal bid and ask should pass, got: {e_eq}")

        # 3. Equal bid == ask passes for options
        p_eq_opt = copy.deepcopy(self.base_valid_payload)
        p_eq_opt["options"]["current_expiry"]["24850_CE"]["bid"] = 120.0
        p_eq_opt["options"]["current_expiry"]["24850_CE"]["ask"] = 120.0
        v_eq_opt, e_eq_opt = self.validator.validate(p_eq_opt)
        self.assertTrue(v_eq_opt, f"Equal option bid and ask should pass, got: {e_eq_opt}")

        # 4. Inverted futures bid > ask rejected
        p_cross_fut = copy.deepcopy(self.base_valid_payload)
        p_cross_fut["futures"]["bid"] = 24890.0
        p_cross_fut["futures"]["ask"] = 24880.0
        v_cf, e_cf = self.validator.validate(p_cross_fut)
        self.assertFalse(v_cf)
        self.assertIn("INVALID_PRICE", e_cf)
        self.assertIn("Inverted futures bid-ask spread", e_cf)

        # 5. Inverted option bid > ask rejected
        p_cross_opt = copy.deepcopy(self.base_valid_payload)
        p_cross_opt["options"]["current_expiry"]["24850_CE"]["bid"] = 125.0
        p_cross_opt["options"]["current_expiry"]["24850_CE"]["ask"] = 120.0
        v_co, e_co = self.validator.validate(p_cross_opt)
        self.assertFalse(v_co)
        self.assertIn("INVALID_PRICE", e_co)
        self.assertIn("Inverted option bid-ask spread", e_co)

        # 6. Missing bid or ask passes (neither required unless both present)
        # Only bid present in futures
        p_only_bid_fut = copy.deepcopy(self.base_valid_payload)
        del p_only_bid_fut["futures"]["ask"]
        v_obf, e_obf = self.validator.validate(p_only_bid_fut)
        self.assertTrue(v_obf, f"Missing ask in futures should pass: {e_obf}")

        # Only ask present in option
        p_only_ask_opt = copy.deepcopy(self.base_valid_payload)
        del p_only_ask_opt["options"]["current_expiry"]["24850_CE"]["bid"]
        v_oao, e_oao = self.validator.validate(p_only_ask_opt)
        self.assertTrue(v_oao, f"Missing bid in option should pass: {e_oao}")

        # Both bid and ask omitted in option
        p_no_quotes = copy.deepcopy(self.base_valid_payload)
        del p_no_quotes["options"]["current_expiry"]["24850_CE"]["bid"]
        del p_no_quotes["options"]["current_expiry"]["24850_CE"]["ask"]
        v_nq, e_nq = self.validator.validate(p_no_quotes)
        self.assertTrue(v_nq, f"Missing both bid and ask should pass: {e_nq}")

        # 7. Non-finite or negative quote values rejected
        for bad_quote in [-1.0, float("nan"), float("inf"), float("-inf")]:
            # Futures negative/non-finite bid
            p_bad_fb = copy.deepcopy(self.base_valid_payload)
            p_bad_fb["futures"]["bid"] = bad_quote
            v_bfb, e_bfb = self.validator.validate(p_bad_fb)
            self.assertFalse(v_bfb)
            self.assertIn("INVALID_PRICE", e_bfb)

            # Option negative/non-finite ask
            p_bad_oa = copy.deepcopy(self.base_valid_payload)
            p_bad_oa["options"]["current_expiry"]["24850_CE"]["ask"] = bad_quote
            v_boa, e_boa = self.validator.validate(p_bad_oa)
            self.assertFalse(v_boa)
            self.assertIn("INVALID_PRICE", e_boa)

    def test_14_volume_and_open_interest_validation(self):
        """Volume and OI validation: valid integers, zero, negative, boolean, string, nan/inf, optional missing."""
        # 1. Valid positive integers and zero pass
        p_valid_counts = copy.deepcopy(self.base_valid_payload)
        p_valid_counts["futures"]["volume"] = 15000
        p_valid_counts["futures"]["open_interest"] = 120000
        p_valid_counts["futures"]["oi"] = 120000
        p_valid_counts["options"]["current_expiry"]["24850_CE"]["volume"] = 0
        p_valid_counts["options"]["current_expiry"]["24850_CE"]["open_interest"] = 0
        p_valid_counts["options"]["current_expiry"]["24850_CE"]["oi"] = 0
        v_cnt, e_cnt = self.validator.validate(p_valid_counts)
        self.assertTrue(v_cnt, f"Valid non-negative counts should pass: {e_cnt}")

        # 2. Optional missing: omitting volume/oi passes
        p_missing_counts = copy.deepcopy(self.base_valid_payload)
        del p_missing_counts["options"]["current_expiry"]["24850_CE"]["volume"]
        del p_missing_counts["options"]["current_expiry"]["24850_CE"]["open_interest"]
        v_mc, e_mc = self.validator.validate(p_missing_counts)
        self.assertTrue(v_mc, f"Missing optional volume/oi should pass: {e_mc}")

        # 3. Negative counts rejected
        for field in ["volume", "open_interest", "oi"]:
            # In futures
            p_neg_fut = copy.deepcopy(self.base_valid_payload)
            p_neg_fut["futures"][field] = -1
            v_nf, e_nf = self.validator.validate(p_neg_fut)
            self.assertFalse(v_nf)
            self.assertIn("INVALID_COUNT", e_nf)

            # In options contract
            p_neg_opt = copy.deepcopy(self.base_valid_payload)
            p_neg_opt["options"]["current_expiry"]["24850_CE"][field] = -100
            v_no, e_no = self.validator.validate(p_neg_opt)
            self.assertFalse(v_no)
            self.assertIn("INVALID_COUNT", e_no)

        # 4. Booleans rejected as counts
        for b_val in [True, False]:
            p_bool = copy.deepcopy(self.base_valid_payload)
            p_bool["options"]["current_expiry"]["24850_CE"]["volume"] = b_val
            v_b, e_b = self.validator.validate(p_bool)
            self.assertFalse(v_b)
            self.assertIn("INVALID_COUNT", e_b)

        # 5. Strings rejected as counts
        p_str = copy.deepcopy(self.base_valid_payload)
        p_str["options"]["current_expiry"]["24850_CE"]["open_interest"] = "50000"
        v_s, e_s = self.validator.validate(p_str)
        self.assertFalse(v_s)
        self.assertIn("INVALID_COUNT", e_s)

        # 6. NaN / infinity rejected as counts
        for bad_num in [float("nan"), float("inf"), float("-inf"), 120.5]:
            p_bad = copy.deepcopy(self.base_valid_payload)
            p_bad["options"]["current_expiry"]["24850_CE"]["oi"] = bad_num
            v_bn, e_bn = self.validator.validate(p_bad)
            self.assertFalse(v_bn)
            self.assertIn("INVALID_COUNT", e_bn)

    def test_15_boolean_rejection_across_all_numeric_fields(self):
        """Strengthen numeric fields: booleans (True/False) strictly rejected as numeric values."""
        # Spot LTP
        p1 = copy.deepcopy(self.base_valid_payload)
        p1["spot"]["ltp"] = True
        v1, e1 = self.validator.validate(p1)
        self.assertFalse(v1)
        self.assertIn("INVALID_PRICE", e1)

        p1_f = copy.deepcopy(self.base_valid_payload)
        p1_f["spot"]["ltp"] = False
        v1_f, e1_f = self.validator.validate(p1_f)
        self.assertFalse(v1_f)
        self.assertIn("INVALID_PRICE", e1_f)

        # Spot high/low/close
        p2 = copy.deepcopy(self.base_valid_payload)
        p2["spot"]["high"] = True
        v2, e2 = self.validator.validate(p2)
        self.assertFalse(v2)
        self.assertIn("NON_FINITE_VALUE", e2)

        # Futures LTP
        p3 = copy.deepcopy(self.base_valid_payload)
        p3["futures"]["ltp"] = True
        v3, e3 = self.validator.validate(p3)
        self.assertFalse(v3)
        self.assertIn("INVALID_PRICE", e3)

        # Futures bid / ask
        p4 = copy.deepcopy(self.base_valid_payload)
        p4["futures"]["bid"] = True
        v4, e4 = self.validator.validate(p4)
        self.assertFalse(v4)
        self.assertIn("INVALID_PRICE", e4)

        # Option strike
        p5 = copy.deepcopy(self.base_valid_payload)
        p5["options"]["current_expiry"]["24850_CE"]["strike"] = True
        v5, e5 = self.validator.validate(p5)
        self.assertFalse(v5)
        self.assertIn("INVALID_STRIKE", e5)

        # Option LTP
        p6 = copy.deepcopy(self.base_valid_payload)
        p6["options"]["current_expiry"]["24850_CE"]["ltp"] = True
        v6, e6 = self.validator.validate(p6)
        self.assertFalse(v6)
        self.assertIn("INVALID_PRICE", e6)

        # Option bid / ask
        p7 = copy.deepcopy(self.base_valid_payload)
        p7["options"]["current_expiry"]["24850_CE"]["bid"] = True
        v7, e7 = self.validator.validate(p7)
        self.assertFalse(v7)
        self.assertIn("INVALID_PRICE", e7)

        # Option Greeks
        p8 = copy.deepcopy(self.base_valid_payload)
        p8["options"]["current_expiry"]["24850_CE"]["greeks"]["delta"] = True
        v8, e8 = self.validator.validate(p8)
        self.assertFalse(v8)
        self.assertIn("NON_FINITE_VALUE", e8)

        # India VIX
        p9 = copy.deepcopy(self.base_valid_payload)
        p9["indices"]["india_vix"]["ltp"] = True
        v9, e9 = self.validator.validate(p9)
        self.assertFalse(v9)
        self.assertIn("INVALID_PRICE", e9)

    def test_16_expiry_format_validation(self):
        """Expiry validation: accepted ISO (YYYY-MM-DD) and NSE (DDMMMYYYY) formats; malformed, whitespace, ambiguous rejected."""
        # 1. Accepted ISO format passes
        p_iso = copy.deepcopy(self.base_valid_payload)
        p_iso["options"]["current_expiry"]["24850_CE"]["expiry"] = "2026-10-29"
        v_iso, e_iso = self.validator.validate(p_iso)
        self.assertTrue(v_iso, f"Valid ISO expiry should pass: {e_iso}")

        # 2. Accepted NSE format passes
        p_nse = copy.deepcopy(self.base_valid_payload)
        p_nse["options"]["current_expiry"]["24850_CE"]["expiry"] = "28OCT2026"
        v_nse, e_nse = self.validator.validate(p_nse)
        self.assertTrue(v_nse, f"Valid NSE expiry should pass: {e_nse}")

        # 3. Malformed strings rejected
        malformed_expiries = [
            "invalid_expiry",
            "none",
            "NA",
            "2026-13-40",  # invalid month/day
            "32OCT2026",    # invalid day
            "28XYZ2026",    # invalid month abbreviation
        ]
        for bad_exp in malformed_expiries:
            p_bad = copy.deepcopy(self.base_valid_payload)
            p_bad["options"]["current_expiry"]["24850_CE"]["expiry"] = bad_exp
            v_b, e_b = self.validator.validate(p_bad)
            self.assertFalse(v_b, f"Malformed expiry {bad_exp!r} should be rejected")
            self.assertIn("INVALID_EXPIRY_FORMAT", e_b)

        # 4. Whitespace-only and padded strings rejected
        for ws_exp in ["", "   ", "\t\n", " 2026-10-29 "]:
            p_ws = copy.deepcopy(self.base_valid_payload)
            p_ws["options"]["current_expiry"]["24850_CE"]["expiry"] = ws_exp
            v_w, e_w = self.validator.validate(p_ws)
            self.assertFalse(v_w, f"Whitespace expiry {ws_exp!r} should be rejected")
            self.assertTrue("INVALID_EXPIRY_FORMAT" in e_w or "MISSING_EXPIRY" in e_w)

        # 5. Ambiguous date formats rejected (not part of documented contract)
        ambiguous_dates = [
            "01/02/2026",
            "10-29-2026",
            "29-10-2026",
            "2026/10/29",
            "29/10/2026",
        ]
        for amb_date in ambiguous_dates:
            p_amb = copy.deepcopy(self.base_valid_payload)
            p_amb["options"]["current_expiry"]["24850_CE"]["expiry"] = amb_date
            v_a, e_a = self.validator.validate(p_amb)
            self.assertFalse(v_a, f"Ambiguous expiry {amb_date!r} should be rejected")
            self.assertIn("INVALID_EXPIRY_FORMAT", e_a)

        # 6. Top-level current_expiry and next_expiry format validation
        p_top_bad = copy.deepcopy(self.base_valid_payload)
        p_top_bad["current_expiry"] = "bad_top_expiry"
        v_tb, e_tb = self.validator.validate(p_top_bad)
        self.assertFalse(v_tb)
        self.assertIn("INVALID_EXPIRY_FORMAT", e_tb)

        # 7. Futures expiry format validation
        p_fut_bad = copy.deepcopy(self.base_valid_payload)
        p_fut_bad["futures"]["expiry"] = "bad_fut_expiry"
        v_fb, e_fb = self.validator.validate(p_fut_bad)
        self.assertFalse(v_fb)
        self.assertIn("INVALID_EXPIRY_FORMAT", e_fb)


if __name__ == "__main__":
    unittest.main()


