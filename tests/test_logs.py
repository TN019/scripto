import logging

from scripto.core.logs import LOGGER_NAME, setup_logging


def get_logger():
    return logging.getLogger(LOGGER_NAME)


def test_log_file_created_and_written(tmp_path):
    ring = setup_logging(tmp_path)
    get_logger().info("hello disk")
    for handler in get_logger().handlers:
        handler.flush()
    content = (tmp_path / "scripto.log").read_text(encoding="utf-8")
    assert "hello disk" in content
    assert "hello disk" in ring.lines()[-1]


def test_ring_buffer_is_capped(tmp_path):
    ring = setup_logging(tmp_path, ring_capacity=10)
    for i in range(25):
        get_logger().info("line %d", i)
    lines = ring.lines()
    assert len(lines) == 10
    assert "line 24" in lines[-1]
    assert "line 15" in lines[0]


def test_setup_twice_does_not_duplicate_output(tmp_path):
    setup_logging(tmp_path)
    ring = setup_logging(tmp_path)  # second call must replace, not stack
    get_logger().info("only once")
    assert sum("only once" in line for line in ring.lines()) == 1
    for handler in get_logger().handlers:
        handler.flush()
    content = (tmp_path / "scripto.log").read_text(encoding="utf-8")
    assert content.count("only once") == 1
