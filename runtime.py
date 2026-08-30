"""Production application entry point for Gunicorn."""

import atexit

import app as microk3_app


def start_services():
    if microk3_app.ROS_AVAILABLE:
        microk3_app.ros_manager = microk3_app.ROS2Manager(microk3_app.ros_update_callback)
        microk3_app.ros_manager.start()
        microk3_app.logger.info('ROS 2 Manager started')
    else:
        microk3_app.logger.warning('ROS 2 Manager not started: dependencies are unavailable')

    microk3_app.docker_stats_collector = microk3_app.DockerStatsCollector(
        lambda payload: microk3_app.ros_update_callback('container_metrics', payload),
        microk3_app.logger,
    )
    microk3_app.docker_stats_collector.start()

    # Jetson host diagnostics (temps, power, GPU/CPU util+freq)
    try:
        if getattr(microk3_app, "JetsonDiagnosticsCollector", None) is not None:
            microk3_app.jetson_diag_collector = microk3_app.JetsonDiagnosticsCollector(
                lambda payload: microk3_app.ros_update_callback('jetson_diagnostics', payload),
                microk3_app.logger,
            )
            microk3_app.jetson_diag_collector.start()
    except Exception as exc:
        microk3_app.logger.warning("Jetson diagnostics collector not started: %s", exc)


def stop_services():
    if microk3_app.ROS_AVAILABLE and microk3_app.ros_manager:
        microk3_app.ros_manager.stop()
    if microk3_app.docker_stats_collector:
        microk3_app.docker_stats_collector.stop()
    try:
        if getattr(microk3_app, "jetson_diag_collector", None):
            microk3_app.jetson_diag_collector.stop()
    except Exception:
        pass


start_services()
atexit.register(stop_services)

app = microk3_app.app