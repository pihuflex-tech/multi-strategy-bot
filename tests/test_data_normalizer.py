"""
test_data_normalizer.py
Comprehensive offline unit tests for FeedDataNormalizer (Phase 2A).
Covers all 20 required specifications:
1. Valid flat option-chain normalization.
2. Valid expiry-bucketed normalization.
3. Identical normalized results for equivalent flat and bucketed inputs.
4. ISO expiry normalization.
5. NSE-style expiry normalization.
6. Impossible expiry rejection.
7. Unsupported expiry format rejection.
8. Bucket and contract expiry conflicts.
9. Duplicate canonical contract detection.
10. Conflicting open_interest and oi rejection.
11. Matching aliases accepted.
12. Explicit zero OI preserved.
13. Missing OI handled according to actual model contract.
14. Missing futures handled explicitly.
15. Empty option chain does not imply readiness.
16. Malformed nested contract rejection.
17. Non-finite values rejected at normalization boundary.
18. Input dictionary remains unchanged.
19. Correct typed objects and canonical keys.
20. Failure cannot return a partially successful snapshot.
"""

import copy
import math
import unittest

from src.feed.data_normalizer import FeedDataNormalizer, normalize_expiry_date, resolve_open_interest
from src.market.market_snapshot import FuturesQuote, MarketSnapshot, OptionQuote


class TestFeedDataNormalizer(unittest.TestCase):
    """Offline unit test suite for FeedDataNormalizer."""

    def setUp(self):
        self.normalizer = FeedDataNormalizer(allow_empty_options=True)
        self.base_bucketed_payload = {
            "timestamp": "2026-10-25 09:20:00.000",
            "server_status": "RUNNING",
            "spot": {"symbol": "NIFTY 50", "ltp": 24850.0, "high": 24890.0, "low": 24810.0, "net_change": 40.0},
            "current_expiry": "2026-10-29",
            "next_expiry": "2026-11-05",
            "futures": {
                "symbol": "NIFTY_FUT_OCT",
                "ltp": 24880.0,
                "expiry": "2026-10-29",
                "bid": 24879.0,
                "ask": 24881.0,
                "volume": 15000,
                "open_interest": 120000,
                "oi": 120000
            },
            "indices": {"india_vix": {"ltp": 13.5}},
            "options": {
                "current_expiry": {
                    "24850_CE": {
                        "strike": 24850.0,
                        "option_type": "CE",
                        "expiry": "2026-10-29",
                        "ltp": 120.0,
                        "bid": 119.5,
                        "ask": 120.5,
                        "volume": 10000,
                        "open_interest": 50000,
                        "oi": 50000,
                        "greeks": {"delta": 0.50, "gamma": 0.0012, "theta": -10.0, "vega": 8.0, "iv": 14.0}
                    },
                    "24850_PE": {
                        "strike": 24850.0,
                        "option_type": "PE",
                        "expiry": "2026-10-29",
                        "ltp": 115.0,
                        "bid": 114.5,
                        "ask": 115.5,
                        "volume": 9000,
                        "open_interest": 45000,
                        "oi": 45000,
                        "greeks": {"delta": -0.50, "gamma": 0.0012, "theta": -10.0, "vega": 8.0, "iv": 14.5}
                    }
                }
            }
        }

        self.base_flat_payload = {
            "timestamp": "2026-10-25 09:20:00.000",
            "server_status": "RUNNING",
            "spot": {"symbol": "NIFTY 50", "ltp": 24850.0, "high": 24890.0, "low": 24810.0, "net_change": 40.0},
            "current_expiry": "2026-10-29",
            "next_expiry": "2026-11-05",
            "futures": {
                "symbol": "NIFTY_FUT_OCT",
                "ltp": 24880.0,
                "expiry": "2026-10-29",
                "bid": 24879.0,
                "ask": 24881.0,
                "volume": 15000,
                "open_interest": 120000,
                "oi": 120000
            },
            "indices": {"india_vix": {"ltp": 13.5}},
            "options": {
                "24850_CE": {
                    "strike": 24850.0,
                    "option_type": "CE",
                    "expiry": "2026-10-29",
                    "ltp": 120.0,
                    "bid": 119.5,
                    "ask": 120.5,
                    "volume": 10000,
                    "open_interest": 50000,
                    "oi": 50000,
                    "greeks": {"delta": 0.50, "gamma": 0.0012, "theta": -10.0, "vega": 8.0, "iv": 14.0}
                },
                "24850_PE": {
                    "strike": 24850.0,
                    "option_type": "PE",
                    "expiry": "2026-10-29",
                    "ltp": 115.0,
                    "bid": 114.5,
                    "ask": 115.5,
                    "volume": 9000,
                    "open_interest": 45000,
                    "oi": 45000,
                    "greeks": {"delta": -0.50, "gamma": 0.0012, "theta": -10.0, "vega": 8.0, "iv": 14.5}
                }
            }
        }

    def test_01_valid_flat_option_chain_normalization(self):
        """1. Valid flat option-chain normalization produces typed MarketSnapshot with canonical keys."""
        ok, snapshot, err = self.normalizer.normalize(self.base_flat_payload)
        self.assertTrue(ok, f"Expected success, got error: {err}")
        self.assertIsNone(err)
        self.assertIsInstance(snapshot, MarketSnapshot)
        self.assertEqual(snapshot.spot_price, 24850.0)
        self.assertEqual(len(snapshot.option_chain), 2)
        self.assertIn("24850_CE_2026-10-29", snapshot.option_chain)
        self.assertIn("24850_PE_2026-10-29", snapshot.option_chain)

    def test_02_valid_expiry_bucketed_normalization(self):
        """2. Valid expiry-bucketed normalization produces typed MarketSnapshot with canonical keys."""
        ok, snapshot, err = self.normalizer.normalize(self.base_bucketed_payload)
        self.assertTrue(ok, f"Expected success, got error: {err}")
        self.assertIsNone(err)
        self.assertIsInstance(snapshot, MarketSnapshot)
        self.assertEqual(snapshot.spot_price, 24850.0)
        self.assertEqual(len(snapshot.option_chain), 2)
        self.assertIn("24850_CE_2026-10-29", snapshot.option_chain)
        self.assertIn("24850_PE_2026-10-29", snapshot.option_chain)

    def test_03_identical_normalized_results_for_equivalent_flat_and_bucketed_inputs(self):
        """3. Identical normalized results for equivalent flat and bucketed inputs."""
        ok_flat, snap_flat, err_flat = self.normalizer.normalize(self.base_flat_payload)
        ok_buck, snap_buck, err_buck = self.normalizer.normalize(self.base_bucketed_payload)

        self.assertTrue(ok_flat)
        self.assertTrue(ok_buck)
        self.assertEqual(snap_flat.spot_price, snap_buck.spot_price)
        self.assertEqual(snap_flat.current_expiry, snap_buck.current_expiry)
        self.assertEqual(snap_flat.next_expiry, snap_buck.next_expiry)
        self.assertEqual(snap_flat.india_vix, snap_buck.india_vix)
        self.assertEqual(set(snap_flat.option_chain.keys()), set(snap_buck.option_chain.keys()))

        for k in snap_flat.option_chain:
            q_f = snap_flat.option_chain[k]
            q_b = snap_buck.option_chain[k]
            self.assertEqual(q_f.strike, q_b.strike)
            self.assertEqual(q_f.option_type, q_b.option_type)
            self.assertEqual(q_f.expiry, q_b.expiry)
            self.assertEqual(q_f.ltp, q_b.ltp)
            self.assertEqual(q_f.open_interest, q_b.open_interest)
            self.assertEqual(q_f.delta, q_b.delta)

    def test_04_iso_expiry_normalization(self):
        """4. ISO expiry normalization preserves YYYY-MM-DD exactly."""
        ok, norm, err = normalize_expiry_date("2026-10-29")
        self.assertTrue(ok)
        self.assertEqual(norm, "2026-10-29")
        self.assertIsNone(err)

    def test_05_nse_style_expiry_normalization(self):
        """5. NSE-style expiry normalization converts DDMMMYYYY to ISO YYYY-MM-DD."""
        ok, norm, err = normalize_expiry_date("28OCT2026")
        self.assertTrue(ok)
        self.assertEqual(norm, "2026-10-28")
        self.assertIsNone(err)

        ok2, norm2, _ = normalize_expiry_date("05NOV2026")
        self.assertTrue(ok2)
        self.assertEqual(norm2, "2026-11-05")

        # In payload normalization
        p = copy.deepcopy(self.base_flat_payload)
        p["options"]["24850_CE"]["expiry"] = "28OCT2026"
        p["options"]["24850_PE"]["expiry"] = "28OCT2026"
        p["current_expiry"] = "28OCT2026"
        ok_p, snap_p, _ = self.normalizer.normalize(p)
        self.assertTrue(ok_p)
        self.assertEqual(snap_p.current_expiry, "2026-10-28")
        self.assertIn("24850_CE_2026-10-28", snap_p.option_chain)

    def test_06_impossible_expiry_rejection(self):
        """6. Impossible calendar dates rejected with IMPOSSIBLE_DATE."""
        impossible_cases = ["2026-02-30", "2026-13-40", "32OCT2026", "29FEB2025"]
        for bad_date in impossible_cases:
            ok, norm, err = normalize_expiry_date(bad_date)
            self.assertFalse(ok)
            self.assertIsNone(norm)
            self.assertIn("IMPOSSIBLE_DATE", err)

        # In payload
        p = copy.deepcopy(self.base_flat_payload)
        p["options"]["24850_CE"]["expiry"] = "2026-02-30"
        ok_p, snap_p, err_p = self.normalizer.normalize(p)
        self.assertFalse(ok_p)
        self.assertIsNone(snap_p)
        self.assertIn("IMPOSSIBLE_DATE", err_p)

    def test_07_unsupported_expiry_format_rejection(self):
        """7. Unsupported or ambiguous expiry formats rejected with UNSUPPORTED_EXPIRY_FORMAT."""
        unsupported = ["01/02/2026", "2026/10/29", "29-10-2026", "OCT282026", "invalid"]
        for bad_fmt in unsupported:
            ok, norm, err = normalize_expiry_date(bad_fmt)
            self.assertFalse(ok)
            self.assertIsNone(norm)
            self.assertIn("UNSUPPORTED_EXPIRY_FORMAT", err)

    def test_08_bucket_and_contract_expiry_conflicts(self):
        """8. Bucket and contract expiry conflicts rejected with EXPIRY_CONFLICT."""
        p = copy.deepcopy(self.base_bucketed_payload)
        # Bucket is current_expiry (top current_expiry is 2026-10-29), but contract specifies 2026-11-05
        p["options"]["current_expiry"]["24850_CE"]["expiry"] = "2026-11-05"
        ok, snap, err = self.normalizer.normalize(p)
        self.assertFalse(ok)
        self.assertIsNone(snap)
        self.assertIn("EXPIRY_CONFLICT", err)

        # Explicit date bucket name conflicting with contract expiry
        p2 = {
            "timestamp": "2026-10-25 09:20:00.000",
            "spot": {"symbol": "NIFTY 50", "ltp": 24850.0},
            "options": {
                "2026-10-29": {
                    "24850_CE": {"strike": 24850.0, "option_type": "CE", "expiry": "2026-11-05", "ltp": 120.0}
                }
            }
        }
        ok2, snap2, err2 = self.normalizer.normalize(p2)
        self.assertFalse(ok2)
        self.assertIsNone(snap2)
        self.assertIn("EXPIRY_CONFLICT", err2)

    def test_09_duplicate_canonical_contract_detection(self):
        """9. Duplicate canonical contract keys detected and rejected without silent overwrite."""
        # Flat payload with two entries that produce identical canonical keys
        p = copy.deepcopy(self.base_flat_payload)
        p["options"]["24850_CE_copy"] = {
            "strike": 24850.0, "option_type": "CE", "expiry": "2026-10-29",
            "ltp": 125.0, "bid": 124.0, "ask": 126.0
        }
        ok, snap, err = self.normalizer.normalize(p)
        self.assertFalse(ok)
        self.assertIsNone(snap)
        self.assertTrue("DUPLICATE_CONTRACT" in err or "DUPLICATE_CONTRACT_CONFLICT" in err)

    def test_10_conflicting_open_interest_and_oi_rejection(self):
        """10. Conflicting open_interest and oi rejected with CONFLICTING_OI_ALIASES."""
        p = copy.deepcopy(self.base_flat_payload)
        p["options"]["24850_CE"]["open_interest"] = 50000
        p["options"]["24850_CE"]["oi"] = 60000  # Conflicting!
        ok, snap, err = self.normalizer.normalize(p)
        self.assertFalse(ok)
        self.assertIsNone(snap)
        self.assertIn("CONFLICTING_OI_ALIASES", err)

        # In futures
        p_fut = copy.deepcopy(self.base_flat_payload)
        p_fut["futures"]["open_interest"] = 120000
        p_fut["futures"]["oi"] = 130000  # Conflicting!
        ok_f, snap_f, err_f = self.normalizer.normalize(p_fut)
        self.assertFalse(ok_f)
        self.assertIsNone(snap_f)
        self.assertIn("CONFLICTING_OI_ALIASES", err_f)

    def test_11_matching_aliases_accepted(self):
        """11. Matching open_interest and oi aliases are accepted."""
        p = copy.deepcopy(self.base_flat_payload)
        p["options"]["24850_CE"]["open_interest"] = 75000
        p["options"]["24850_CE"]["oi"] = 75000
        ok, snap, err = self.normalizer.normalize(p)
        self.assertTrue(ok)
        self.assertIsNone(err)
        quote = snap.option_chain["24850_CE_2026-10-29"]
        self.assertEqual(quote.open_interest, 75000)
        self.assertEqual(quote.oi, 75000)

    def test_12_explicit_zero_oi_preserved(self):
        """12. Explicit zero OI is preserved as zero and not overwritten or omitted."""
        p = copy.deepcopy(self.base_flat_payload)
        p["options"]["24850_CE"]["open_interest"] = 0
        del p["options"]["24850_CE"]["oi"]
        ok, snap, err = self.normalizer.normalize(p)
        self.assertTrue(ok)
        quote = snap.option_chain["24850_CE_2026-10-29"]
        self.assertEqual(quote.open_interest, 0)
        self.assertEqual(quote.oi, 0)

    def test_13_missing_oi_handled_according_to_actual_model_contract(self):
        """13. Missing OI handled according to model default (0) without inventing non-zero data."""
        p = copy.deepcopy(self.base_flat_payload)
        del p["options"]["24850_CE"]["open_interest"]
        del p["options"]["24850_CE"]["oi"]
        ok, snap, err = self.normalizer.normalize(p)
        self.assertTrue(ok)
        quote = snap.option_chain["24850_CE_2026-10-29"]
        self.assertEqual(quote.open_interest, 0)
        self.assertEqual(quote.oi, 0)

    def test_14_missing_futures_handled_explicitly(self):
        """14. Missing futures handled explicitly (snapshot.futures is None)."""
        p = copy.deepcopy(self.base_flat_payload)
        del p["futures"]
        ok, snap, err = self.normalizer.normalize(p)
        self.assertTrue(ok)
        self.assertIsNone(snap.futures)

    def test_15_empty_option_chain_does_not_imply_readiness(self):
        """15. Empty option chain normalizes safely but does not imply readiness (chain is empty)."""
        p = copy.deepcopy(self.base_flat_payload)
        p["options"] = {}
        ok, snap, err = self.normalizer.normalize(p)
        self.assertTrue(ok)
        self.assertEqual(len(snap.option_chain), 0)
        # Attempting to fetch option from empty chain returns None
        self.assertIsNone(snap.get_option(24850, "CE"))

    def test_16_malformed_nested_contract_rejection(self):
        """16. Malformed nested contract rejection (non-dict contract, missing required fields)."""
        # Non-dict contract
        p1 = copy.deepcopy(self.base_flat_payload)
        p1["options"]["24850_CE"] = 120.0
        ok1, snap1, err1 = self.normalizer.normalize(p1)
        self.assertFalse(ok1)
        self.assertIsNone(snap1)
        self.assertIn("MALFORMED_CONTRACT", err1)

        # Missing strike
        p2 = copy.deepcopy(self.base_flat_payload)
        del p2["options"]["24850_CE"]["strike"]
        ok2, snap2, err2 = self.normalizer.normalize(p2)
        self.assertFalse(ok2)
        self.assertIsNone(snap2)
        self.assertIn("MALFORMED_CONTRACT", err2)

        # Missing ltp
        p3 = copy.deepcopy(self.base_flat_payload)
        del p3["options"]["24850_CE"]["ltp"]
        ok3, snap3, err3 = self.normalizer.normalize(p3)
        self.assertFalse(ok3)
        self.assertIsNone(snap3)
        self.assertIn("MALFORMED_CONTRACT", err3)

    def test_17_non_finite_values_rejected_at_normalization_boundary(self):
        """17. Non-finite values rejected at normalization boundary."""
        for bad_val in [float("nan"), float("inf"), float("-inf")]:
            p = copy.deepcopy(self.base_flat_payload)
            p["options"]["24850_CE"]["ltp"] = bad_val
            ok, snap, err = self.normalizer.normalize(p)
            self.assertFalse(ok)
            self.assertIsNone(snap)
            self.assertIn("INVALID_PRICE", err)

            # Non-finite Greek
            p_g = copy.deepcopy(self.base_flat_payload)
            p_g["options"]["24850_CE"]["greeks"]["delta"] = bad_val
            ok_g, snap_g, err_g = self.normalizer.normalize(p_g)
            self.assertFalse(ok_g)
            self.assertIsNone(snap_g)
            self.assertIn("NON_FINITE_VALUE", err_g)

    def test_18_input_dictionary_remains_unchanged(self):
        """18. Input dictionary is strictly immutable across normalization."""
        original_flat = copy.deepcopy(self.base_flat_payload)
        ok_flat, snap_flat, err_flat = self.normalizer.normalize(self.base_flat_payload)
        self.assertTrue(ok_flat)
        self.assertEqual(self.base_flat_payload, original_flat, "Input dictionary was mutated during flat normalization!")

        original_buck = copy.deepcopy(self.base_bucketed_payload)
        ok_buck, snap_buck, err_buck = self.normalizer.normalize(self.base_bucketed_payload)
        self.assertTrue(ok_buck)
        self.assertEqual(self.base_bucketed_payload, original_buck, "Input dictionary was mutated during bucketed normalization!")

    def test_19_correct_typed_objects_and_canonical_keys(self):
        """19. Correct typed objects (MarketSnapshot, OptionQuote, FuturesQuote) and canonical keys."""
        ok, snap, err = self.normalizer.normalize(self.base_flat_payload)
        self.assertTrue(ok)
        self.assertIsInstance(snap, MarketSnapshot)
        self.assertIsInstance(snap.futures, FuturesQuote)

        expected_key = "24850_CE_2026-10-29"
        self.assertIn(expected_key, snap.option_chain)
        quote = snap.option_chain[expected_key]
        self.assertIsInstance(quote, OptionQuote)
        self.assertEqual(quote.strike, 24850.0)
        self.assertEqual(quote.strike_price, 24850.0)
        self.assertEqual(quote.option_type, "CE")
        self.assertEqual(quote.expiry, "2026-10-29")
        self.assertEqual(quote.ltp, 120.0)
        self.assertEqual(quote.bid, 119.5)
        self.assertEqual(quote.ask, 120.5)
        self.assertEqual(quote.volume, 10000)
        self.assertEqual(quote.open_interest, 50000)
        self.assertEqual(quote.delta, 0.50)
        self.assertEqual(quote.iv, 14.0)

    def test_20_failure_cannot_return_partially_successful_snapshot(self):
        """20. Normalization failure strictly returns (False, None, error), never a partial snapshot."""
        # Corrupt one contract out of two
        p = copy.deepcopy(self.base_flat_payload)
        p["options"]["24850_PE"]["ltp"] = -5.0  # Invalid!
        ok, snap, err = self.normalizer.normalize(p)
        self.assertFalse(ok)
        self.assertIsNone(snap, "Partial snapshot must NOT be returned on failure!")
        self.assertIsNotNone(err)
        self.assertIn("INVALID_PRICE", err)

    def test_21_reproduction_1_next_expiry_flat_contract_missing_expiry_fails_closed(self):
        """21. Reproduction 1: Next-expiry flat contract missing expiry must not silently become current-expiry."""
        payload = {
            "timestamp": "2026-10-25 09:20:00.000",
            "spot": {"symbol": "NIFTY 50", "ltp": 24850.0},
            "current_expiry": "2026-10-29",
            "next_expiry": "2026-11-05",
            "options": {
                "24800_CE_NEXT": {
                    "strike": 24800.0,
                    "option_type": "CE",
                    "ltp": 195.0
                }
            }
        }
        ok, snap, err = self.normalizer.normalize(payload)
        self.assertFalse(ok)
        self.assertIsNone(snap)
        self.assertIsNotNone(err)
        self.assertIn("AMBIGUOUS_EXPIRY_IN_FLAT_LAYOUT", err)
        # Crucial invariant: it must never silently produce canonical key 24800_CE_2026-10-29

    def test_22_reproduction_2_two_different_expiry_contracts_not_merged_into_one_canonical_key(self):
        """22. Reproduction 2: Two different-expiry contracts must not be incorrectly merged into one canonical key."""
        payload = {
            "timestamp": "2026-10-25 09:20:00.000",
            "spot": {"symbol": "NIFTY 50", "ltp": 24850.0},
            "current_expiry": "2026-10-29",
            "next_expiry": "2026-11-05",
            "options": {
                "24800_CE_CUR": {
                    "strike": 24800.0,
                    "option_type": "CE",
                    "ltp": 120.0
                },
                "24800_CE_NXT": {
                    "strike": 24800.0,
                    "option_type": "CE",
                    "ltp": 195.0
                }
            }
        }
        ok, snap, err = self.normalizer.normalize(payload)
        self.assertFalse(ok)
        self.assertIsNone(snap)
        self.assertIsNotNone(err)
        self.assertIn("AMBIGUOUS_EXPIRY_IN_FLAT_LAYOUT", err)

    def test_23_valid_explicit_expiry_flat_contracts_normalize_cleanly(self):
        """23. Valid explicit-expiry flat contracts normalize correctly into distinct canonical keys."""
        payload = {
            "timestamp": "2026-10-25 09:20:00.000",
            "spot": {"symbol": "NIFTY 50", "ltp": 24850.0},
            "current_expiry": "2026-10-29",
            "next_expiry": "2026-11-05",
            "options": {
                "24800_CE_CUR": {
                    "strike": 24800.0,
                    "option_type": "CE",
                    "expiry": "2026-10-29",
                    "ltp": 120.0,
                    "bid": 119.5,
                    "ask": 120.5
                },
                "24800_CE_NXT": {
                    "strike": 24800.0,
                    "option_type": "CE",
                    "expiry": "2026-11-05",
                    "ltp": 195.0,
                    "bid": 194.5,
                    "ask": 195.5
                }
            }
        }
        ok, snap, err = self.normalizer.normalize(payload)
        self.assertTrue(ok, f"Expected success, got: {err}")
        self.assertIsNone(err)
        self.assertIsNotNone(snap)
        self.assertIn("24800_CE_2026-10-29", snap.option_chain)
        self.assertIn("24800_CE_2026-11-05", snap.option_chain)
        self.assertEqual(snap.option_chain["24800_CE_2026-10-29"].ltp, 120.0)
        self.assertEqual(snap.option_chain["24800_CE_2026-11-05"].ltp, 195.0)

    def test_24_valid_bucketed_contracts_multi_expiry_normalize_cleanly(self):
        """24. Valid bucketed contracts (current_expiry, next_expiry, date bucket) normalize unambiguously."""
        payload = {
            "timestamp": "2026-10-25 09:20:00.000",
            "spot": {"symbol": "NIFTY 50", "ltp": 24850.0},
            "current_expiry": "2026-10-29",
            "next_expiry": "2026-11-05",
            "options": {
                "current_expiry": {
                    "24800_CE": {"strike": 24800.0, "option_type": "CE", "ltp": 120.0}
                },
                "next_expiry": {
                    "24800_CE": {"strike": 24800.0, "option_type": "CE", "ltp": 195.0}
                },
                "2026-11-12": {
                    "24800_CE": {"strike": 24800.0, "option_type": "CE", "ltp": 250.0}
                }
            }
        }
        ok, snap, err = self.normalizer.normalize(payload)
        self.assertTrue(ok, f"Expected success, got: {err}")
        self.assertIsNone(err)
        self.assertIsNotNone(snap)
        self.assertIn("24800_CE_2026-10-29", snap.option_chain)
        self.assertIn("24800_CE_2026-11-05", snap.option_chain)
        self.assertIn("24800_CE_2026-11-12", snap.option_chain)
        self.assertEqual(snap.option_chain["24800_CE_2026-10-29"].ltp, 120.0)
        self.assertEqual(snap.option_chain["24800_CE_2026-11-05"].ltp, 195.0)
        self.assertEqual(snap.option_chain["24800_CE_2026-11-12"].ltp, 250.0)

    def test_25_unambiguous_single_expiry_flat_contract_and_strict_flag(self):
        """25. Unambiguous single-expiry flat contract resolves safely; strict_flat_expiry rejects it."""
        single_exp_payload = {
            "timestamp": "2026-10-25 09:20:00.000",
            "spot": {"symbol": "NIFTY 50", "ltp": 24850.0},
            "current_expiry": "2026-10-29",
            "options": {
                "24850_CE": {"strike": 24850.0, "option_type": "CE", "ltp": 120.0}
            }
        }
        # Default normalizer: safe single-expiry context resolves to current_expiry
        ok, snap, err = self.normalizer.normalize(single_exp_payload)
        self.assertTrue(ok, f"Expected success, got: {err}")
        self.assertIn("24850_CE_2026-10-29", snap.option_chain)

        # Strict normalizer: requires explicit expiry even in single-expiry flat payload
        strict_norm = FeedDataNormalizer(allow_empty_options=True, strict_flat_expiry=True)
        ok_s, snap_s, err_s = strict_norm.normalize(single_exp_payload)
        self.assertFalse(ok_s)
        self.assertIsNone(snap_s)
        self.assertIn("AMBIGUOUS_EXPIRY_IN_FLAT_LAYOUT", err_s)


class TestFeedServerSchemaNormalizationPhase1(unittest.TestCase):
    """
    Focused Phase 1 offline regression tests verifying NIFTY Feed Server schema normalization.
    Validates:
    1. Valid feed server payload containing all top-level fields:
       server_status, timestamp, reconnect_count, last_error, spot, indices, heavyweights, futures, contracts, options.
    2. Extraction of next_expiry from verified nested options location.
    3. Extraction of monthly_expiry from verified nested options location.
    4. Extraction of nearest futures token, symbol, and expiry from futures['near'].
    5. Missing next_expiry or monthly_expiry handled safely without fabricated dates.
    6. Missing or incomplete futures['near'] handled safely (snapshot.futures is None).
    7. Missing optional Greeks or option fields handled safely with defaults.
    8. Correct CE/PE distinction and strike association with canonical keys.
    9. IV percentage values (e.g. 14.25 representing 14.25%) preserved accurately.
    10. Full backward compatibility with existing legacy payload schemas.
    """

    def setUp(self):
        self.normalizer = FeedDataNormalizer(allow_empty_options=True)
        # Sanitized, offline fixture reflecting the exact NIFTY Feed Server snapshot schema
        self.feed_server_snapshot = {
            "server_status": "CONNECTED",
            "timestamp": "2026-10-27 09:30:00.123",
            "reconnect_count": 0,
            "last_error": None,
            "spot": {
                "symbol": "NIFTY",
                "ltp": 24850.25,
                "high": 24900.0,
                "low": 24800.0,
                "net_change": 50.25,
                "last_trade_time": "2026-10-27 09:30:00"
            },
            "indices": {
                "india_vix": {"ltp": 13.8, "net_change": -0.2}
            },
            "heavyweights": {
                "RELIANCE": {"ltp": 2950.0, "net_change": 15.0, "volume": 500000},
                "HDFCBANK": {"ltp": 1680.0, "net_change": 8.0, "volume": 750000}
            },
            "futures": {
                "contracts": {},
                "near": {
                    "token": "48704",
                    "symbol": "NIFTY26OCTFUT",
                    "expiry": "27OCT2026",
                    "ltp": 24880.0,
                    "open_interest": 120000,
                    "volume": 15000,
                    "net_change": 45.0
                },
                "next": {
                    "token": "48705",
                    "symbol": "NIFTY26NOV26FUT",
                    "expiry": "26NOV2026",
                    "ltp": 24950.0,
                    "open_interest": 30000,
                    "volume": 2000,
                    "net_change": 40.0
                },
                "ltp": 24880.0,
                "open_interest": 120000,
                "volume": 15000,
                "net_change": 45.0,
                "symbol": "NIFTY26OCTFUT"
            },
            "contracts": {
                "24850": {"CE": {"ltp": 120.0}, "PE": {"ltp": 115.0}},
                "24000": {"PE": {"ltp": 12.0}}
            },
            "options": {
                "current_expiry": {
                    "24850_CE": {
                        "token": "1001",
                        "symbol": "NIFTY26OCT24850CE",
                        "strike": 24850.0,
                        "option_type": "CE",
                        "expiry": "27OCT2026",
                        "ltp": 120.0,
                        "bid": 119.5,
                        "ask": 120.5,
                        "volume": 10000,
                        "open_interest": 50000,
                        "high": 125.0,
                        "low": 110.0,
                        "net_change": 5.0,
                        "greeks": {
                            "iv": 14.25,
                            "delta": 0.5123,
                            "gamma": 0.001234,
                            "theta": -10.5,
                            "vega": 8.2
                        }
                    },
                    "24850_PE": {
                        "token": "1002",
                        "symbol": "NIFTY26OCT24850PE",
                        "strike": 24850.0,
                        "option_type": "PE",
                        "expiry": "27OCT2026",
                        "ltp": 115.0,
                        "bid": 114.5,
                        "ask": 115.5,
                        "volume": 9000,
                        "open_interest": 45000,
                        "high": 120.0,
                        "low": 105.0,
                        "net_change": -4.0,
                        "greeks": {
                            "iv": 14.85,
                            "delta": -0.4877,
                            "gamma": 0.001234,
                            "theta": -10.2,
                            "vega": 8.1
                        }
                    }
                },
                "wings": {
                    "24000_PE": {
                        "token": "1003",
                        "symbol": "NIFTY26OCT24000PE",
                        "strike": 24000.0,
                        "option_type": "PE",
                        "expiry": "27OCT2026",
                        "ltp": 12.0,
                        "bid": 11.5,
                        "ask": 12.5,
                        "volume": 5000,
                        "open_interest": 20000,
                        "high": 15.0,
                        "low": 10.0,
                        "net_change": -1.0,
                        "greeks": {
                            "iv": 16.50,
                            "delta": -0.05,
                            "gamma": 0.0003,
                            "theta": -2.0,
                            "vega": 1.5
                        }
                    }
                },
                "next_expiry": {
                    "24850_CE": {
                        "token": "2001",
                        "symbol": "NIFTY05NOV24850CE",
                        "strike": 24850.0,
                        "option_type": "CE",
                        "expiry": "05NOV2026",
                        "ltp": 180.0,
                        "bid": 179.0,
                        "ask": 181.0,
                        "volume": 3000,
                        "open_interest": 15000,
                        "high": 185.0,
                        "low": 170.0,
                        "net_change": 8.0,
                        "greeks": {
                            "iv": 14.50,
                            "delta": 0.52,
                            "gamma": 0.0009,
                            "theta": -8.0,
                            "vega": 11.0
                        }
                    }
                },
                "monthly_expiry": {
                    "25000_CE": {
                        "token": "3001",
                        "symbol": "NIFTY26NOV25000CE",
                        "strike": 25000.0,
                        "option_type": "CE",
                        "expiry": "26NOV2026",
                        "ltp": 210.0,
                        "bid": 209.0,
                        "ask": 211.0,
                        "volume": 2000,
                        "open_interest": 25000,
                        "high": 215.0,
                        "low": 200.0,
                        "net_change": 10.0,
                        "greeks": {
                            "iv": 15.10,
                            "delta": 0.49,
                            "gamma": 0.0007,
                            "theta": -6.5,
                            "vega": 14.0
                        }
                    }
                }
            }
        }

    def test_01_feed_server_payload_with_all_top_level_fields(self):
        """1. Full Feed Server payload containing all 10 expected top-level fields normalizes correctly."""
        ok, snap, err = self.normalizer.normalize(self.feed_server_snapshot)
        self.assertTrue(ok, f"Expected success, got: {err}")
        self.assertIsNone(err)
        self.assertIsInstance(snap, MarketSnapshot)
        self.assertEqual(snap.spot_price, 24850.25)
        self.assertEqual(snap.india_vix, 13.8)
        self.assertEqual(snap.current_expiry, "2026-10-27")
        self.assertEqual(snap.next_expiry, "2026-11-05")
        self.assertEqual(snap.monthly_expiry, "2026-11-26")
        self.assertIsNotNone(snap.futures)
        self.assertEqual(snap.futures.ltp, 24880.0)
        self.assertEqual(snap.futures.token, "48704")
        self.assertEqual(snap.futures.symbol, "NIFTY26OCTFUT")
        self.assertEqual(snap.futures.expiry, "2026-10-27")
        # Check all 5 contracts normalized with canonical keys
        self.assertIn("24850_CE_2026-10-27", snap.option_chain)
        self.assertIn("24850_PE_2026-10-27", snap.option_chain)
        self.assertIn("24000_PE_2026-10-27", snap.option_chain)
        self.assertIn("24850_CE_2026-11-05", snap.option_chain)
        self.assertIn("25000_CE_2026-11-26", snap.option_chain)
        self.assertEqual(len(snap.option_chain), 5)

    def test_02_correct_extraction_of_nested_next_expiry(self):
        """2. Correct extraction of next-expiry data from nested options['next_expiry']."""
        p = copy.deepcopy(self.feed_server_snapshot)
        self.assertNotIn("next_expiry", p)  # absent at root
        ok, snap, err = self.normalizer.normalize(p)
        self.assertTrue(ok)
        self.assertEqual(snap.next_expiry, "2026-11-05")
        quote = snap.option_chain["24850_CE_2026-11-05"]
        self.assertEqual(quote.expiry, "2026-11-05")
        self.assertEqual(quote.ltp, 180.0)

    def test_03_correct_extraction_of_nested_monthly_expiry(self):
        """3. Correct extraction of monthly-expiry data from nested options['monthly_expiry']."""
        p = copy.deepcopy(self.feed_server_snapshot)
        self.assertNotIn("monthly_expiry", p)  # absent at root
        ok, snap, err = self.normalizer.normalize(p)
        self.assertTrue(ok)
        self.assertEqual(snap.monthly_expiry, "2026-11-26")
        quote = snap.option_chain["25000_CE_2026-11-26"]
        self.assertEqual(quote.expiry, "2026-11-26")
        self.assertEqual(quote.ltp, 210.0)

    def test_04_correct_extraction_of_nearest_futures_token_and_expiry(self):
        """4. Correct extraction of nearest futures token and expiry from futures['near']."""
        p = copy.deepcopy(self.feed_server_snapshot)
        ok, snap, err = self.normalizer.normalize(p)
        self.assertTrue(ok)
        self.assertIsNotNone(snap.futures)
        self.assertEqual(snap.futures.token, "48704")
        self.assertEqual(snap.futures.symbol, "NIFTY26OCTFUT")
        self.assertEqual(snap.futures.expiry, "2026-10-27")
        self.assertEqual(snap.futures.ltp, 24880.0)
        self.assertEqual(snap.futures.volume, 15000)
        self.assertEqual(snap.futures.open_interest, 120000)

    def test_05_missing_next_or_monthly_expiry_safe_handling(self):
        """5. Missing next_expiry or monthly_expiry handled safely without fabricating dates."""
        p = copy.deepcopy(self.feed_server_snapshot)
        p["options"]["next_expiry"] = {}
        p["options"]["monthly_expiry"] = {}
        ok, snap, err = self.normalizer.normalize(p)
        self.assertTrue(ok)
        self.assertEqual(snap.next_expiry, "")
        self.assertEqual(snap.monthly_expiry, "")
        self.assertEqual(snap.current_expiry, "2026-10-27")

    def test_06_missing_or_incomplete_futures_near_handled_safely(self):
        """6. Missing or incomplete futures['near'] handled safely (snapshot.futures is None)."""
        # Empty near dict
        p1 = copy.deepcopy(self.feed_server_snapshot)
        p1["futures"] = {"near": {}}
        ok1, snap1, err1 = self.normalizer.normalize(p1)
        self.assertTrue(ok1)
        self.assertIsNone(snap1.futures)

        # Incomplete near dict (missing ltp)
        p2 = copy.deepcopy(self.feed_server_snapshot)
        p2["futures"] = {"near": {"token": "48704", "symbol": "NIFTY_FUT"}}
        ok2, snap2, err2 = self.normalizer.normalize(p2)
        self.assertTrue(ok2)
        self.assertIsNone(snap2.futures)

        # Near is absent
        p3 = copy.deepcopy(self.feed_server_snapshot)
        p3["futures"] = {"contracts": {}}
        ok3, snap3, err3 = self.normalizer.normalize(p3)
        self.assertTrue(ok3)
        self.assertIsNone(snap3.futures)

    def test_07_missing_optional_greeks_and_option_fields(self):
        """7. Missing optional Greeks or option fields default safely to 0.0 without errors."""
        p = copy.deepcopy(self.feed_server_snapshot)
        del p["options"]["current_expiry"]["24850_CE"]["greeks"]
        ok, snap, err = self.normalizer.normalize(p)
        self.assertTrue(ok)
        quote = snap.option_chain["24850_CE_2026-10-27"]
        self.assertEqual(quote.delta, 0.0)
        self.assertEqual(quote.gamma, 0.0)
        self.assertEqual(quote.theta, 0.0)
        self.assertEqual(quote.vega, 0.0)
        self.assertEqual(quote.iv, 0.0)

    def test_08_correct_ce_pe_and_strike_association(self):
        """8. Correct CE/PE and strike association maintained across option chain."""
        ok, snap, err = self.normalizer.normalize(self.feed_server_snapshot)
        self.assertTrue(ok)
        ce_quote = snap.get_option(24850, "CE", expiry="2026-10-27")
        pe_quote = snap.get_option(24850, "PE", expiry="2026-10-27")
        self.assertIsNotNone(ce_quote)
        self.assertIsNotNone(pe_quote)
        self.assertEqual(ce_quote.option_type, "CE")
        self.assertEqual(ce_quote.strike_price, 24850.0)
        self.assertEqual(ce_quote.ltp, 120.0)
        self.assertEqual(pe_quote.option_type, "PE")
        self.assertEqual(pe_quote.strike_price, 24850.0)
        self.assertEqual(pe_quote.ltp, 115.0)

    def test_09_iv_percentage_values_preserved(self):
        """9. IV percentage values (e.g. 14.25 representing 14.25%) remain correctly interpreted."""
        ok, snap, err = self.normalizer.normalize(self.feed_server_snapshot)
        self.assertTrue(ok)
        ce_quote = snap.option_chain["24850_CE_2026-10-27"]
        self.assertEqual(ce_quote.iv, 14.25)
        self.assertNotEqual(ce_quote.iv, 0.1425)

        wings_quote = snap.option_chain["24000_PE_2026-10-27"]
        self.assertEqual(wings_quote.iv, 16.50)

    def test_10_existing_supported_legacy_payloads_continue_to_work(self):
        """10. Existing supported legacy payloads with root current_expiry/next_expiry/futures work."""
        legacy_payload = {
            "timestamp": "2026-10-25 09:20:00.000",
            "server_status": "RUNNING",
            "spot": {"symbol": "NIFTY 50", "ltp": 24850.0},
            "current_expiry": "2026-10-29",
            "next_expiry": "2026-11-05",
            "futures": {
                "symbol": "NIFTY_FUT_OCT",
                "ltp": 24880.0,
                "expiry": "2026-10-29",
                "volume": 15000,
                "open_interest": 120000
            },
            "indices": {"india_vix": {"ltp": 13.5}},
            "options": {
                "24850_CE": {
                    "strike": 24850.0,
                    "option_type": "CE",
                    "expiry": "2026-10-29",
                    "ltp": 120.0,
                    "volume": 10000,
                    "open_interest": 50000,
                    "greeks": {"delta": 0.50, "iv": 14.0}
                }
            }
        }
        ok, snap, err = self.normalizer.normalize(legacy_payload)
        self.assertTrue(ok)
        self.assertEqual(snap.current_expiry, "2026-10-29")
        self.assertEqual(snap.next_expiry, "2026-11-05")
        self.assertIsNotNone(snap.futures)
        self.assertEqual(snap.futures.symbol, "NIFTY_FUT_OCT")
        self.assertEqual(snap.futures.expiry, "2026-10-29")
        self.assertEqual(snap.futures.ltp, 24880.0)
        self.assertIn("24850_CE_2026-10-29", snap.option_chain)


if __name__ == "__main__":
    unittest.main()
