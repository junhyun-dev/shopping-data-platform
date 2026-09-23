from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import tarfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .config import KAFKA_DISTRIBUTION_ROOT, KAFKA_RUNTIME_CONFIG
from .kafka_lab import KafkaLabFailure


def load_runtime_config(path: Path = KAFKA_RUNTIME_CONFIG) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "apache_kafka_version",
        "scala_version",
        "archive_file",
        "download_url",
        "sha512",
    }
    missing = sorted(required - set(config))
    if missing:
        raise KafkaLabFailure(f"Kafka runtime config is missing: {missing}")
    return config


def _sha512(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        for member in members:
            member_path = (destination / member.name).resolve()
            if not member_path.is_relative_to(destination):
                raise KafkaLabFailure(
                    f"Kafka archive member escapes extraction root: {member.name}"
                )
            if member.issym() or member.islnk():
                raise KafkaLabFailure(
                    f"Kafka archive contains an unsupported link: {member.name}"
                )
        bundle.extractall(destination, members=members)


def prepare_distribution(
    runtime_root: Path = KAFKA_DISTRIBUTION_ROOT,
) -> dict[str, Any]:
    config = load_runtime_config()
    runtime_root = runtime_root.resolve()
    downloads = runtime_root / "downloads"
    distributions = runtime_root / "dist"
    archive = downloads / config["archive_file"]
    distribution_name = (
        f"kafka_{config['scala_version']}-{config['apache_kafka_version']}"
    )
    distribution = distributions / distribution_name
    downloads.mkdir(parents=True, exist_ok=True)
    distributions.mkdir(parents=True, exist_ok=True)

    if archive.exists():
        actual = _sha512(archive)
        if actual != config["sha512"]:
            raise KafkaLabFailure(
                f"existing Kafka archive checksum mismatch: expected {config['sha512']}, got {actual}"
            )
    else:
        temporary = archive.with_suffix(f"{archive.suffix}.part")
        temporary.unlink(missing_ok=True)
        try:
            with urllib.request.urlopen(config["download_url"], timeout=30) as response:
                with temporary.open("xb") as output:
                    shutil.copyfileobj(response, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
            actual = _sha512(temporary)
            if actual != config["sha512"]:
                raise KafkaLabFailure(
                    f"downloaded Kafka archive checksum mismatch: expected {config['sha512']}, got {actual}"
                )
            os.replace(temporary, archive)
        finally:
            temporary.unlink(missing_ok=True)

    if distribution.exists():
        required = [
            distribution / "bin/kafka-storage.sh",
            distribution / "bin/kafka-server-start.sh",
        ]
        if not all(path.is_file() for path in required):
            raise KafkaLabFailure(
                f"existing Kafka distribution is incomplete: {distribution}"
            )
    else:
        _safe_extract(archive, distributions)

    return {
        "version": config["apache_kafka_version"],
        "archive": str(archive),
        "archive_sha512": _sha512(archive),
        "distribution": str(distribution),
        "download_url": config["download_url"],
    }


def _assert_loopback_port_available(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise KafkaLabFailure(f"loopback port is already in use: {port}") from exc


@dataclass
class LocalKafkaBroker:
    process: subprocess.Popen[bytes]
    log_stream: BinaryIO
    log_path: Path
    bootstrap_servers: str
    distribution: Path
    cluster_id: str

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self.log_stream.close()


def _broker_log_tail(path: Path, lines: int = 60) -> str:
    if not path.exists():
        return "<broker log was not created>"
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def start_local_broker(
    *,
    run_root: Path,
    broker_port: int = 19092,
    controller_port: int = 19093,
    heap_megabytes: int = 256,
    startup_timeout_seconds: float = 30.0,
) -> LocalKafkaBroker:
    prepared = prepare_distribution()
    distribution = Path(prepared["distribution"])
    run_root = run_root.resolve()
    if run_root.exists():
        if any(run_root.iterdir()):
            raise KafkaLabFailure(
                f"Kafka run root must be new or empty; refusing to reformat {run_root}"
            )
    else:
        run_root.mkdir(parents=True)
    _assert_loopback_port_available(broker_port)
    _assert_loopback_port_available(controller_port)

    data_dir = run_root / "broker-data"
    config_path = run_root / "server.properties"
    log_path = run_root / "broker.log"
    config_path.write_text(
        "\n".join(
            [
                "process.roles=broker,controller",
                "node.id=1",
                f"controller.quorum.bootstrap.servers=127.0.0.1:{controller_port}",
                f"listeners=PLAINTEXT://127.0.0.1:{broker_port},CONTROLLER://127.0.0.1:{controller_port}",
                f"advertised.listeners=PLAINTEXT://127.0.0.1:{broker_port}",
                "inter.broker.listener.name=PLAINTEXT",
                "controller.listener.names=CONTROLLER",
                "listener.security.protocol.map=CONTROLLER:PLAINTEXT,PLAINTEXT:PLAINTEXT",
                f"log.dirs={data_dir}",
                "num.partitions=1",
                "num.network.threads=2",
                "num.io.threads=2",
                "num.recovery.threads.per.data.dir=1",
                "offsets.topic.replication.factor=1",
                "share.coordinator.state.topic.replication.factor=1",
                "share.coordinator.state.topic.min.isr=1",
                "transaction.state.log.replication.factor=1",
                "transaction.state.log.min.isr=1",
                "group.initial.rebalance.delay.ms=0",
                "auto.create.topics.enable=false",
                "log.retention.hours=1",
                "log.segment.bytes=16777216",
                "delete.topic.enable=true",
                "",
            ]
        ),
        encoding="utf-8",
    )

    storage = distribution / "bin/kafka-storage.sh"
    cluster_id = subprocess.run(
        [str(storage), "random-uuid"],
        cwd=distribution,
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    ).stdout.strip()
    subprocess.run(
        [
            str(storage),
            "format",
            "--standalone",
            "--cluster-id",
            cluster_id,
            "--config",
            str(config_path),
        ],
        cwd=distribution,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )

    log_stream = log_path.open("xb")
    env = os.environ.copy()
    env["KAFKA_HEAP_OPTS"] = f"-Xms{heap_megabytes}m -Xmx{heap_megabytes}m"
    process = subprocess.Popen(
        [
            str(distribution / "bin/kafka-server-start.sh"),
            str(config_path),
        ],
        cwd=distribution,
        env=env,
        stdout=log_stream,
        stderr=subprocess.STDOUT,
    )
    broker = LocalKafkaBroker(
        process=process,
        log_stream=log_stream,
        log_path=log_path,
        bootstrap_servers=f"127.0.0.1:{broker_port}",
        distribution=distribution,
        cluster_id=cluster_id,
    )

    try:
        from confluent_kafka.admin import AdminClient

        admin = AdminClient(
            {
                "bootstrap.servers": broker.bootstrap_servers,
                "client.id": "shopping-broker-readiness",
                "socket.timeout.ms": 2000,
            }
        )
        deadline = time.monotonic() + startup_timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise KafkaLabFailure(
                    f"Kafka broker exited with {process.returncode}:\n{_broker_log_tail(log_path)}"
                )
            try:
                metadata = admin.list_topics(timeout=1)
                if metadata.brokers:
                    return broker
            except Exception:
                pass
        raise KafkaLabFailure(
            f"Kafka broker was not ready within {startup_timeout_seconds}s:\n"
            f"{_broker_log_tail(log_path)}"
        )
    except Exception:
        broker.stop()
        raise
