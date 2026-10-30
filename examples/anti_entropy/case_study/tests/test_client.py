"""Tests for DataConsumer."""

from examples.anti_entropy.case_study.client import DataConsumer


def test_consumer_normal_flow() -> None:
    consumer = DataConsumer()
    res = consumer.process_message("HDR|hello world|CRC32")
    assert res["status"] == "ok"
    assert res["content"] == "hello world"
    assert res["length"] == 11
