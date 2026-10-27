"""
test_feed_parser.py
Comprehensive offline unit tests for FeedMessageParser.
Validates multipart frames, topic checks, UTF-8 decodes, malformed JSON,
non-standard constants (NaN/Infinity), scalar/list rejection, and state independence.
"""

import json
import unittest

from src.feed.message_parser import FeedMessageParser


class TestFeedMessageParser(unittest.TestCase):
    """Offline unit test suite for FeedMessageParser."""

    def setUp(self):
        self.parser = FeedMessageParser(expected_topic=b"NIFTY_MARKET_TICK")
        self.valid_payload = {
            "timestamp": "2026-10-25 09:20:00.000",
            "server_status": "RUNNING",
            "spot": {"symbol": "NIFTY 50", "ltp": 24850.0},
            "options": {
                "current_expiry": {
                    "24850_CE": {"strike": 24850.0, "option_type": "CE", "expiry": "2026-10-29", "ltp": 120.0}
                }
            }
        }
        self.valid_frames = [
            b"NIFTY_MARKET_TICK",
            json.dumps(self.valid_payload).encode("utf-8")
        ]

    def test_01_valid_multipart_message(self):
        """1. Valid multipart message: returns success, parsed dict, and None error."""
        success, payload, err = self.parser.parse_multipart(self.valid_frames)
        self.assertTrue(success)
        self.assertIsNone(err)
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["spot"]["ltp"], 24850.0)
        self.assertEqual(payload["timestamp"], "2026-10-25 09:20:00.000")

    def test_02_wrong_topic(self):
        """2. Wrong topic: rejects message with explicit INVALID_TOPIC error."""
        wrong_frames = [b"WRONG_TOPIC_TICK", self.valid_frames[1]]
        success, payload, err = self.parser.parse_multipart(wrong_frames)
        self.assertFalse(success)
        self.assertIsNone(payload)
        self.assertIn("INVALID_TOPIC", err)

    def test_03_incorrect_frame_count(self):
        """3. Incorrect frame count: rejects 1 frame, 3 frames, empty list, and non-sequence."""
        # 1 frame
        s1, p1, e1 = self.parser.parse_multipart([b"NIFTY_MARKET_TICK"])
        self.assertFalse(s1)
        self.assertIn("INVALID_FRAME_COUNT", e1)

        # 3 frames
        s3, p3, e3 = self.parser.parse_multipart([b"NIFTY_MARKET_TICK", b"{}", b"EXTRA"])
        self.assertFalse(s3)
        self.assertIn("INVALID_FRAME_COUNT", e3)

        # 0 frames
        s0, p0, e0 = self.parser.parse_multipart([])
        self.assertFalse(s0)
        self.assertIn("INVALID_FRAME_COUNT", e0)

        # Non-list structure
        sn, pn, en = self.parser.parse_multipart("not_a_list")
        self.assertFalse(sn)
        self.assertIn("INVALID_FRAME_STRUCTURE", en)

        # Non-bytes frame
        sb, pb, eb = self.parser.parse_multipart(["topic_str", b"{}"])
        self.assertFalse(sb)
        self.assertIn("INVALID_TOPIC_TYPE", eb)

    def test_04_invalid_utf8(self):
        """4. Invalid UTF-8: rejects non-UTF-8 bytes with INVALID_ENCODING."""
        invalid_bytes = b"NIFTY \xff\xfe invalid utf8"
        bad_frames = [b"NIFTY_MARKET_TICK", invalid_bytes]
        success, payload, err = self.parser.parse_multipart(bad_frames)
        self.assertFalse(success)
        self.assertIsNone(payload)
        self.assertIn("INVALID_ENCODING", err)

    def test_05_malformed_json(self):
        """5. Malformed JSON: rejects incomplete, truncated, or syntactically invalid JSON."""
        test_cases = [
            b"{spot: 24850.0, incomplete",
            b'{"spot": 24850.0,',
            b'{"spot": 24850.0}} extra',
            b""
        ]
        for bad_json in test_cases:
            frames = [b"NIFTY_MARKET_TICK", bad_json]
            success, payload, err = self.parser.parse_multipart(frames)
            self.assertFalse(success)
            self.assertIsNone(payload)
            self.assertIn("MALFORMED_JSON", err)

    def test_06_json_scalar_or_list_instead_of_object(self):
        """6. JSON scalar or list instead of object: rejects list, string, number, and boolean."""
        test_payloads = [
            b"[1, 2, 3]",
            b'"just a string"',
            b"12345",
            b"true",
            b"null"
        ]
        for non_obj in test_payloads:
            frames = [b"NIFTY_MARKET_TICK", non_obj]
            success, payload, err = self.parser.parse_multipart(frames)
            self.assertFalse(success)
            self.assertIsNone(payload)
            self.assertIn("INVALID_PAYLOAD_TYPE", err)

    def test_07_nan_and_infinity_rejected(self):
        """9. NaN and positive/negative Infinity: rejected at parse time."""
        # NaN in JSON
        nan_json = b'{"timestamp": "2026-10-25 09:20:00", "spot": {"ltp": NaN}}'
        s_nan, p_nan, e_nan = self.parser.parse_multipart([b"NIFTY_MARKET_TICK", nan_json])
        self.assertFalse(s_nan)
        self.assertIn("MALFORMED_JSON", e_nan)
        self.assertIn("NON_STANDARD_CONSTANT_REJECTED", e_nan)

        # Infinity in JSON
        inf_json = b'{"timestamp": "2026-10-25 09:20:00", "spot": {"ltp": Infinity}}'
        s_inf, p_inf, e_inf = self.parser.parse_multipart([b"NIFTY_MARKET_TICK", inf_json])
        self.assertFalse(s_inf)
        self.assertIn("MALFORMED_JSON", e_inf)
        self.assertIn("NON_STANDARD_CONSTANT_REJECTED", e_inf)

        # -Infinity in JSON
        ninf_json = b'{"timestamp": "2026-10-25 09:20:00", "spot": {"ltp": -Infinity}}'
        s_ninf, p_ninf, e_ninf = self.parser.parse_multipart([b"NIFTY_MARKET_TICK", ninf_json])
        self.assertFalse(s_ninf)
        self.assertIn("MALFORMED_JSON", e_ninf)
        self.assertIn("NON_STANDARD_CONSTANT_REJECTED", e_ninf)

    def test_08_repeated_parser_calls_without_shared_state_contamination(self):
        """17. Repeated parser calls without shared-state contamination."""
        # 1. Valid call
        s1, p1, e1 = self.parser.parse_multipart(self.valid_frames)
        self.assertTrue(s1)
        self.assertIsNotNone(p1)

        # 2. Invalid call
        s2, p2, e2 = self.parser.parse_multipart([b"NIFTY_MARKET_TICK", b"{corrupt"])
        self.assertFalse(s2)
        self.assertIsNone(p2)

        # 3. Valid call again
        s3, p3, e3 = self.parser.parse_multipart(self.valid_frames)
        self.assertTrue(s3)
        self.assertIsNotNone(p3)
        self.assertEqual(p3["spot"]["ltp"], 24850.0)

        # 4. Another invalid call (wrong topic)
        s4, p4, e4 = self.parser.parse_multipart([b"WRONG", self.valid_frames[1]])
        self.assertFalse(s4)

        # 5. Final valid call via alias `parse`
        s5, p5, e5 = self.parser.parse(self.valid_frames)
        self.assertTrue(s5)
        self.assertIsNotNone(p5)

    def test_09_custom_expected_topic(self):
        """Configurable topic check works cleanly."""
        custom_parser = FeedMessageParser(expected_topic=b"CUSTOM_TICK")
        frames = [b"CUSTOM_TICK", self.valid_frames[1]]
        success, payload, err = custom_parser.parse(frames)
        self.assertTrue(success)
        self.assertIsNone(err)

    def test_10_large_valid_json_payload(self):
        """16. Large but bounded valid payloads: handles 100 option strikes swiftly."""
        large_payload = {
            "timestamp": "2026-10-25 09:20:00.000",
            "spot": {"symbol": "NIFTY 50", "ltp": 24800.0},
            "options": {"current_expiry": {}}
        }
        for strike in range(22000, 27000, 50):
            large_payload["options"]["current_expiry"][f"{strike}_CE"] = {
                "strike": strike, "option_type": "CE", "expiry": "2026-10-29",
                "ltp": 100.0, "bid": 99.5, "ask": 100.5, "volume": 5000,
                "open_interest": 20000, "greeks": {"delta": 0.5, "iv": 14.0}
            }
        frames = [b"NIFTY_MARKET_TICK", json.dumps(large_payload).encode("utf-8")]
        success, payload, err = self.parser.parse(frames)
        self.assertTrue(success)
        self.assertEqual(len(payload["options"]["current_expiry"]), 100)

    def test_11_payload_size_bounding_exact_and_over_limit(self):
        """Payload byte limits: payload exactly at limit passes; 1 byte over limit rejected with PAYLOAD_TOO_LARGE."""
        # Baseline minimal valid JSON object
        base_obj = {"t": "X"}
        base_json = json.dumps(base_obj).encode("utf-8")
        limit = len(base_json)

        bounded_parser = FeedMessageParser(max_payload_bytes=limit)

        # 1. Exactly at limit: passes
        s_exact, p_exact, e_exact = bounded_parser.parse([b"NIFTY_MARKET_TICK", base_json])
        self.assertTrue(s_exact, f"Payload exactly at limit should pass, got error: {e_exact}")
        self.assertIsNone(e_exact)
        self.assertEqual(p_exact, base_obj)

        # 2. Exactly one byte over limit: fails with PAYLOAD_TOO_LARGE
        over_limit_json = json.dumps({"t": "XY"}).encode("utf-8")
        self.assertEqual(len(over_limit_json), limit + 1)
        s_over, p_over, e_over = bounded_parser.parse([b"NIFTY_MARKET_TICK", over_limit_json])
        self.assertFalse(s_over)
        self.assertIsNone(p_over)
        self.assertIn("PAYLOAD_TOO_LARGE", e_over)
        self.assertIn(f"exceeds maximum allowed limit of {limit} bytes", e_over)

    def test_12_oversized_payload_rejected_before_decoding_or_parsing(self):
        """Oversized payload rejected strictly before UTF-8 decoding or JSON parsing."""
        small_limit = 50
        bounded_parser = FeedMessageParser(max_payload_bytes=small_limit)

        # Payload contains invalid UTF-8 bytes and malformed JSON syntax, but is oversized (100 bytes)
        invalid_utf8_oversized = b"\xff\xfe" + b"X" * 100
        self.assertGreater(len(invalid_utf8_oversized), small_limit)

        s, p, err = bounded_parser.parse([b"NIFTY_MARKET_TICK", invalid_utf8_oversized])
        self.assertFalse(s)
        self.assertIsNone(p)
        # Must be rejected as PAYLOAD_TOO_LARGE, proving it never reached decode (which would say INVALID_ENCODING)
        self.assertIn("PAYLOAD_TOO_LARGE", err)
        self.assertNotIn("INVALID_ENCODING", err)
        self.assertNotIn("MALFORMED_JSON", err)

    def test_13_invalid_max_payload_bytes_configuration(self):
        """Invalid max_payload_bytes configuration rejected with ValueError."""
        invalid_configs = [0, -1, -5000, True, False, "5MB", 5.5, None, []]
        for bad_cfg in invalid_configs:
            with self.assertRaises(ValueError, msg=f"Should reject max_payload_bytes={bad_cfg!r}"):
                FeedMessageParser(max_payload_bytes=bad_cfg)

    def test_14_default_max_payload_bytes(self):
        """Default max_payload_bytes is 5 MiB (5,242,880 bytes)."""
        parser = FeedMessageParser()
        self.assertEqual(parser.max_payload_bytes, 5 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()


