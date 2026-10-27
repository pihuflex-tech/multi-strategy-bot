"""
message_parser.py
Parser for multipart ZeroMQ frames and JSON payloads.
Rejects malformed JSON, invalid frame structures, non-UTF8 encodings, and non-standard JSON constants.
Strictly decoupled: does not connect to ZeroMQ, write files, or mutate global state.
"""

import json
from typing import Any, Dict, List, Optional, Tuple, Union


class FeedMessageParser:
    """
    Parser for multipart ZeroMQ market tick frames.
    Validates frame count, topic bytes, UTF-8 encoding, and JSON object structures.
    """

    DEFAULT_TOPIC: bytes = b"NIFTY_MARKET_TICK"
    DEFAULT_MAX_PAYLOAD_BYTES: int = 5 * 1024 * 1024  # 5 MiB conservative default limit

    def __init__(
        self,
        expected_topic: bytes = DEFAULT_TOPIC,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
    ):
        """
        Initializes parser with expected topic and maximum payload size guard.

        Parameters:
            expected_topic: Bytes topic to match.
            max_payload_bytes: Positive integer maximum allowed payload byte length.
        """
        self.expected_topic = bytes(expected_topic)
        if isinstance(max_payload_bytes, bool) or not isinstance(max_payload_bytes, int) or max_payload_bytes <= 0:
            raise ValueError(f"max_payload_bytes must be a positive integer, got {max_payload_bytes!r}")
        self.max_payload_bytes = max_payload_bytes

    def parse_multipart(self, frames: Any) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """
        Parses a multipart message representation.

        Parameters:
            frames: Sequence of frames (typically [topic_bytes, payload_bytes]).

        Returns:
            Tuple of (success: bool, payload: Optional[Dict], error_code: Optional[str])
        """
        if not isinstance(frames, (list, tuple)):
            return False, None, f"INVALID_FRAME_STRUCTURE: Expected list or tuple, got {type(frames).__name__}"

        if len(frames) != 2:
            return False, None, f"INVALID_FRAME_COUNT: Expected 2 frames, got {len(frames)}"

        topic_frame, payload_frame = frames[0], frames[1]

        if not isinstance(topic_frame, (bytes, bytearray)):
            return False, None, f"INVALID_TOPIC_TYPE: Expected bytes, got {type(topic_frame).__name__}"

        if not isinstance(payload_frame, (bytes, bytearray)):
            return False, None, f"INVALID_PAYLOAD_TYPE: Expected bytes, got {type(payload_frame).__name__}"

        if len(payload_frame) > self.max_payload_bytes:
            return False, None, f"PAYLOAD_TOO_LARGE: Payload size {len(payload_frame)} bytes exceeds maximum allowed limit of {self.max_payload_bytes} bytes"

        if bytes(topic_frame) != self.expected_topic:
            return False, None, f"INVALID_TOPIC: Expected {self.expected_topic!r}, got {bytes(topic_frame)!r}"

        try:
            payload_str = payload_frame.decode("utf-8")
        except UnicodeDecodeError as e:
            return False, None, f"INVALID_ENCODING: UTF-8 decode failed: {e.reason}"

        # Parse JSON rejecting non-standard constants (NaN, Infinity, -Infinity)
        def _reject_constant(constant: str) -> None:
            raise ValueError(f"NON_STANDARD_CONSTANT_REJECTED: {constant}")

        try:
            parsed = json.loads(payload_str, parse_constant=_reject_constant)
        except json.JSONDecodeError as e:
            return False, None, f"MALFORMED_JSON: line {e.lineno} col {e.colno}"
        except ValueError as e:
            return False, None, f"MALFORMED_JSON: {e}"

        if not isinstance(parsed, dict):
            return False, None, f"INVALID_PAYLOAD_TYPE: Expected JSON object/dict, got {type(parsed).__name__}"

        return True, parsed, None

    def parse(self, frames: Any) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """Alias for parse_multipart."""
        return self.parse_multipart(frames)


# Backward-compatibility alias
MessageParserPlaceholder = FeedMessageParser

