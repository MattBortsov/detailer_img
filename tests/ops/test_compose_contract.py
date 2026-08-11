"""Static production Compose and Nginx contracts."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
SERVICES = COMPOSE["services"]


def test_singleton_runtime_graph_is_complete() -> None:
    expected = {
        "postgres",
        "redis",
        "clamav",
        "storage-init",
        "migrate",
        "api",
        "bot",
        "daily-stats",
        "relay",
        "worker",
        "nginx",
    }
    assert set(SERVICES) == expected
    assert SERVICES["worker"]["command"] == ["python", "-m", "car_wrap.worker.main"]
    assert SERVICES["bot"]["command"] == ["python", "-m", "car_wrap.bot.main"]
    assert SERVICES["daily-stats"]["command"] == [
        "python",
        "-m",
        "car_wrap.stats.main",
    ]
    assert SERVICES["relay"]["command"] == ["python", "-m", "car_wrap.jobs.main"]


def test_only_nginx_publishes_a_runtime_port() -> None:
    published = {
        name: service.get("ports", [])
        for name, service in SERVICES.items()
        if service.get("ports")
    }
    assert published == {"nginx": ["443:443"]}
    assert COMPOSE["networks"]["backend"]["internal"] is True


def test_long_running_services_have_bounded_logs_and_restart() -> None:
    long_running = (
        "postgres",
        "redis",
        "clamav",
        "api",
        "bot",
        "daily-stats",
        "relay",
        "worker",
        "nginx",
    )
    for name in long_running:
        service = SERVICES[name]
        assert service["restart"] == "unless-stopped"
        assert service["logging"]["driver"] == "json-file"
        assert service["logging"]["options"] == {"max-size": "10m", "max-file": "3"}
        assert service["mem_limit"]
        assert service["ulimits"]["core"] == {"soft": 0, "hard": 0}


def test_media_privacy_volumes_are_narrow() -> None:
    assert "postgres_data" in COMPOSE["volumes"]
    assert SERVICES["worker"]["volumes"] == [
        "custom_color_data:/var/lib/car-wrap/custom-colors:ro"
    ]
    volume_names = set(COMPOSE["volumes"])
    assert not any("vehicle" in name or "result" in name for name in volume_names)


def test_nginx_is_tls_only_and_forwards_trusted_scheme() -> None:
    config = (ROOT / "nginx/car-wrap.conf").read_text()
    assert "listen 443 ssl;" in config
    assert "listen 80" not in config
    assert "proxy_pass http://api:8000;" in config
    assert "proxy_set_header X-Forwarded-Proto https;" in config
    assert "client_max_body_size 9m;" in config
    assert "$request_method $uri $server_protocol" in config
    assert "$request_uri" not in config
    assert "89-167-101-93.sslip.io/fullchain.pem" in config
