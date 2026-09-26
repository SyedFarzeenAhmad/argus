"""MQTT → ingest pipeline.

    uv run python -m argus_api.ingest.worker

Topic tree (docs/04):

    argus/v1/{fleet}/{device_id}/obs        QoS 1   observation
    argus/v1/{fleet}/{device_id}/pass       QoS 1   segment_pass
    argus/v1/{fleet}/{device_id}/incident   QoS 2   incident      ← exactly-once
    argus/v1/{fleet}/{device_id}/tlm        QoS 0   telemetry     ← retained, lossy is fine

A persistent session (``clean_start=False``) means QoS 1/2 messages published while the
worker was down are delivered when it comes back, which is the broker half of the edge's
store-and-forward spool.
"""

from __future__ import annotations

import argparse
import signal
import ssl

import structlog

from argus_api.core.config import get_settings
from argus_api.core.logging import configure_logging
from argus_api.ingest.pipeline import Ingestor

log = structlog.get_logger()

SUFFIX_TO_KIND = {
    "obs": "observation",
    "pass": "segment_pass",
    "incident": "incident",
    "tlm": "telemetry",
}
SUBSCRIPTIONS = {"obs": 1, "pass": 1, "incident": 2, "tlm": 0}


def parse_topic(topic: str) -> tuple[str, str] | None:
    """``argus/v1/{fleet}/{device}/{suffix}`` → ``(kind, device_id)``."""
    parts = topic.split("/")
    if len(parts) != 5 or parts[0] != "argus" or parts[1] != "v1":
        return None
    kind = SUFFIX_TO_KIND.get(parts[4])
    return (kind, parts[3]) if kind else None


def main() -> None:
    import paho.mqtt.client as mqtt
    from paho.mqtt.enums import CallbackAPIVersion

    ap = argparse.ArgumentParser(description="ARGUS MQTT ingest worker")
    ap.add_argument("--fleet", default=None, help="fleet id to subscribe to (default: all)")
    args = ap.parse_args()

    configure_logging()
    cfg = get_settings()
    fleet = args.fleet or cfg.mqtt_fleet
    ingestor = Ingestor()

    client = mqtt.Client(
        CallbackAPIVersion.VERSION2, client_id=cfg.mqtt_client_id, protocol=mqtt.MQTTv5
    )
    if cfg.mqtt_tls:
        client.tls_set(
            ca_certs=cfg.mqtt_ca_file,
            certfile=cfg.mqtt_cert_file,
            keyfile=cfg.mqtt_key_file,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )

    def on_connect(c, _userdata, _flags, reason_code, _props):
        if reason_code.is_failure:
            log.error("mqtt.connect_failed", reason=str(reason_code))
            return
        c.subscribe(
            [(f"argus/v1/{fleet}/+/{suffix}", qos) for suffix, qos in SUBSCRIPTIONS.items()]
        )
        log.info("mqtt.subscribed", fleet=fleet, host=cfg.mqtt_host, port=cfg.mqtt_port)

    def on_message(_c, _userdata, msg):
        parsed = parse_topic(msg.topic)
        if parsed is None:
            log.warning("mqtt.unknown_topic", topic=msg.topic)
            return
        kind, device = parsed
        try:
            result = ingestor.handle(kind, msg.payload, topic=msg.topic, topic_device=device)
        except Exception:  # never let one bad message kill the subscriber
            log.exception("ingest.crashed", topic=msg.topic)
            return
        if result.status != "stored":
            log.info("ingest.result", kind=kind, status=result.status, reason=result.reason)

    client.on_connect = on_connect
    client.on_message = on_message
    from paho.mqtt.packettypes import PacketTypes
    from paho.mqtt.properties import Properties

    props = Properties(PacketTypes.CONNECT)
    props.SessionExpiryInterval = 24 * 3600
    client.connect(cfg.mqtt_host, cfg.mqtt_port, keepalive=30, clean_start=False, properties=props)

    def stop(*_):
        log.info("mqtt.stopping")
        client.disconnect()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    client.loop_forever(retry_first_connection=True)


if __name__ == "__main__":
    main()
