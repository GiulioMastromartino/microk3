import json
import pytest
import numpy as np
from app import app, get_default_data
from base64 import b64encode


@pytest.fixture
def client(tmp_path):
    """Create test client"""
    app.config['TESTING'] = True
    app.config['RATELIMIT_ENABLED'] = False  # Disable rate limiting for tests
    app.config['DATA_FILE'] = str(tmp_path / 'system_data.json')
    app.config['LOG_FILE'] = str(tmp_path / 'app.log')

    import app as microk3_app
    original_ros_available = microk3_app.ROS_AVAILABLE
    microk3_app.app.config['DATA_FILE'] = app.config['DATA_FILE']
    microk3_app.app.config['LOG_FILE'] = app.config['LOG_FILE']
    microk3_app.system_data = get_default_data()
    microk3_app.ros_inspector_state = microk3_app.get_empty_ros_state()
    microk3_app.ros_manager = None

    with app.test_client() as client:
        yield client

    microk3_app.ROS_AVAILABLE = original_ros_available


@pytest.fixture
def auth_headers():
    """Create authentication headers"""
    credentials = b64encode(b"admin:changeme").decode('utf-8')
    return {'Authorization': f'Basic {credentials}'}


def test_home_page(client):
    """Test home page loads"""
    response = client.get('/')
    assert response.status_code == 200


def test_nodes_page(client):
    """Test nodes page loads"""
    response = client.get('/nodes')
    assert response.status_code == 200


def test_ros_page(client):
    """Test ROS page loads"""
    response = client.get('/ros')
    assert response.status_code == 200


def test_rover_map_page(client):
    """Test the interactive rover-map page loads."""
    response = client.get('/rover-map')
    assert response.status_code == 200
    assert b'rover-map-canvas' in response.data


def test_occupancy_map_page(client):
    """Test the independent OccupancyGrid page loads."""
    response = client.get('/occupancy-map')
    assert response.status_code == 200
    assert b'occupancy-map-canvas' in response.data


def test_health_check(client):
    """Test health endpoint"""
    response = client.get('/health')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['status'] == 'healthy'


def test_ssh_terminal_session_id_requires_authentication(client):
    """The privileged terminal bootstrap endpoint must never be public."""
    response = client.get('/api/ssh-terminal/session-id')
    assert response.status_code == 401


def test_ssh_terminal_overlay_is_shared_by_base_template(client):
    response = client.get('/')
    assert response.status_code == 200
    assert b'ssh-terminal-toggle' in response.data
    assert b'/ws/ssh-terminal' in response.data


def test_api_nodes(client):
    """Test nodes API endpoint"""
    response = client.get('/api/nodes')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert isinstance(data, list)
    assert len(data) == 0


def test_api_system_status(client):
    """Test system status endpoint"""
    response = client.get('/api/system_status')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'status' in data
    assert 'nodes_online' in data


def test_api_metrics_summary_defaults(client):
    response = client.get('/api/metrics/summary')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['aggregate']['client_to_agent_ms'] is None
    assert data['aggregate']['agent_to_ros_ms'] is None
    assert data['aggregate']['end_to_end_ms'] is None
    assert data['aggregate']['sync_ready'] is False
    assert data['aggregate']['time_sync_rtt_jitter_ms'] == 0.0
    assert data['containers'] == {}
    assert data['topics'] == {}


def test_api_node_detail(client):
    """Test node detail endpoint"""
    response = client.get('/api/nodes/1')
    assert response.status_code == 404


def test_api_node_not_found(client):
    """Test node not found"""
    response = client.get('/api/nodes/999')
    assert response.status_code == 404


def test_update_node_no_auth(client):
    """Test update node without authentication"""
    import app as microk3_app
    microk3_app.system_data['nodes'].append(
        microk3_app.Node(
            id=1,
            name="Node 1",
            status="active",
            type="STM32",
            ram="128KB",
            flash="512KB",
            cpu="Cortex-M7"
        )
    )
    response = client.post('/api/update_node',
                          json={'node_id': 1, 'status': 'standby'},
                          content_type='application/json')
    assert response.status_code == 401


def test_update_node_with_auth(client, auth_headers):
    """Test update node with authentication"""
    import app as microk3_app
    microk3_app.system_data['nodes'].append(
        microk3_app.Node(
            id=1,
            name="Node 1",
            status="active",
            type="STM32",
            ram="128KB",
            flash="512KB",
            cpu="Cortex-M7"
        )
    )
    response = client.post('/api/update_node',
                          headers=auth_headers,
                          json={'node_id': 1, 'status': 'standby'},
                          content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['success'] is True


def test_update_node_invalid_status(client, auth_headers):
    """Test update node with invalid status"""
    import app as microk3_app
    microk3_app.system_data['nodes'].append(
        microk3_app.Node(
            id=1,
            name="Node 1",
            status="active",
            type="STM32",
            ram="128KB",
            flash="512KB",
            cpu="Cortex-M7"
        )
    )
    response = client.post('/api/update_node',
                          headers=auth_headers,
                          json={'node_id': 1, 'status': 'invalid'},
                          content_type='application/json')
    assert response.status_code == 400


def test_update_node_missing_field(client, auth_headers):
    """Test update node with missing field"""
    import app as microk3_app
    microk3_app.system_data['nodes'].append(
        microk3_app.Node(
            id=1,
            name="Node 1",
            status="active",
            type="STM32",
            ram="128KB",
            flash="512KB",
            cpu="Cortex-M7"
        )
    )
    response = client.post('/api/update_node',
                          headers=auth_headers,
                          json={'status': 'active'},
                          content_type='application/json')
    assert response.status_code == 400


def test_add_failure_with_auth(client, auth_headers):
    """Test adding failure with authentication"""
    import app as microk3_app
    microk3_app.system_data['nodes'].append(
        microk3_app.Node(
            id=1,
            name="Node 1",
            status="active",
            type="STM32",
            ram="128KB",
            flash="512KB",
            cpu="Cortex-M7"
        )
    )

    response = client.post('/api/add_failure',
                          headers=auth_headers,
                          json={
                              'node_id': 1,
                              'description': 'Test failure',
                              'status': 'open'
                          },
                          content_type='application/json')
    assert response.status_code == 201
    data = json.loads(response.data)
    assert data['success'] is True


def test_api_ros_graph_disconnected(client):
    """ROS graph endpoint should return stable empty payload when ROS is disconnected."""
    response = client.get('/api/ros/graph')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['ros_connected'] is False
    assert data['nodes'] == []
    assert data['topics'] == []
    assert data['watched_topics'] == {}


def test_api_ros_watch_requires_topic_name(client):
    response = client.post('/api/ros/watch', json={}, content_type='application/json')
    assert response.status_code == 400


def test_api_ros_watch_disconnected(client):
    response = client.post(
        '/api/ros/watch',
        json={'topic_name': '/heartbeat'},
        content_type='application/json'
    )
    assert response.status_code == 503


def test_api_ros_unwatch_missing_topic(client):
    response = client.post('/api/ros/unwatch', json={}, content_type='application/json')
    assert response.status_code == 400


def test_api_ros_topic_samples_not_found(client):
    response = client.get('/api/ros/topics/heartbeat/samples')
    assert response.status_code == 404


def test_api_ros_watch_limit_enforced(client, monkeypatch):
    import app as microk3_app

    for idx in range(microk3_app.ROS_WATCH_LIMIT):
        topic_name = f'/topic_{idx}'
        microk3_app.ros_inspector_state['watched_topics'][topic_name] = {
            'topic_name': topic_name,
            'topic_type': 'std_msgs/msg/String',
            'latest': None,
            'history': [],
            'last_update': None,
            'status': 'idle',
            'error': None,
        }

    response = client.post(
        '/api/ros/watch',
        json={'topic_name': '/overflow'},
        content_type='application/json'
    )
    assert response.status_code == 400


def test_api_ros_watch_and_unwatch_with_fake_manager(client):
    import app as microk3_app

    class FakeRosManager:
        running = True

        def watch_topic(self, topic_name):
            microk3_app.ros_update_callback('watch_started', {
                'topic_name': topic_name,
                'topic_type': 'std_msgs/msg/String',
            })
            return {'success': True, 'topic_name': topic_name, 'topic_type': 'std_msgs/msg/String'}

        def unwatch_topic(self, topic_name):
            microk3_app.ros_update_callback('watch_stopped', {'topic_name': topic_name})
            return {'success': True, 'topic_name': topic_name}

    microk3_app.ROS_AVAILABLE = True
    microk3_app.ros_manager = FakeRosManager()

    watch_response = client.post(
        '/api/ros/watch',
        json={'topic_name': '/heartbeat'},
        content_type='application/json'
    )
    assert watch_response.status_code == 200

    graph_response = client.get('/api/ros/graph')
    graph_data = json.loads(graph_response.data)
    assert '/heartbeat' in graph_data['watched_topics']

    unwatch_response = client.post(
        '/api/ros/unwatch',
        json={'topic_name': '/heartbeat'},
        content_type='application/json'
    )
    assert unwatch_response.status_code == 200


def test_pointcloud_api_with_fake_manager(client):
    import app as microk3_app

    class FakeRosManager:
        running = True

        def __init__(self):
            self.live_started = []
            self.accumulation_started = []

        def start_pointcloud_live(self, topic_name):
            self.live_started.append(topic_name)
            return {'success': True, 'topic_name': topic_name}

        def stop_pointcloud_live(self, topic_name):
            return {'success': True, 'topic_name': topic_name}

        def get_pointcloud_live_snapshot(self, topic_name, max_points):
            assert max_points == 20000
            return {
                'xyz': np.array([[1.0, 2.0, 3.0]], dtype=np.float32),
                'colors': np.array([[10, 20, 30]], dtype=np.uint8),
                'has_color': True,
                'frame_id': 'zed_camera_frame',
                'point_count': 1,
                'point_count_after_downsample': 1,
                'frames_received': 7,
                'last_error': None,
            }

        def start_pointcloud_accumulation(self, topic_name, pose_topic, voxel_size, max_map_points):
            self.accumulation_started.append((topic_name, pose_topic, voxel_size, max_map_points))
            return {'success': True, 'topic_name': topic_name, 'pose_topic': pose_topic}

        def stop_pointcloud_accumulation(self, topic_name):
            return {'success': True, 'topic_name': topic_name}

        def reset_pointcloud_accumulation(self, topic_name):
            return {'success': True, 'topic_name': topic_name}

        def get_pointcloud_accumulation_snapshot(self, topic_name):
            return {
                'points': np.array([[4.0, 5.0, 6.0]], dtype=np.float32),
                'colors': None,
                'has_color': False,
                'point_count': 1,
                'frames_received': 3,
                'frames_dropped_no_pose': 2,
                'last_error': 'No nearby pose',
            }

    manager = FakeRosManager()
    microk3_app.ROS_AVAILABLE = True
    microk3_app.ros_manager = manager

    start_live = client.post('/api/ros/pointcloud/live/start', json={'topic_name': 'cloud'})
    assert start_live.status_code == 200
    assert manager.live_started == ['/cloud']

    latest = client.get('/api/ros/pointcloud/live/zed/points/latest')
    assert latest.status_code == 200
    latest_payload = latest.get_json()
    assert latest_payload['topic_name'] == '/zed/points'
    assert latest_payload['point_count'] == 1
    assert latest_payload['has_color'] is True
    assert latest_payload['positions_base64']
    assert latest_payload['colors_base64']

    start_accumulation = client.post(
        '/api/ros/pointcloud/accumulate/start',
        json={
            'topic_name': '/cloud',
            'pose_topic': 'zed/pose',
            'voxel_size': 0.1,
            'max_map_points': 1000,
        },
    )
    assert start_accumulation.status_code == 200
    assert manager.accumulation_started == [('/cloud', '/zed/pose', 0.1, 1000)]

    map_snapshot = client.get('/api/ros/pointcloud/accumulate/zed/points/snapshot')
    assert map_snapshot.status_code == 200
    map_payload = map_snapshot.get_json()
    assert map_payload['frames_dropped_no_pose'] == 2
    assert map_payload['has_color'] is False
    assert map_payload['last_error'] == 'No nearby pose'


def test_pointcloud_api_validates_parameters(client):
    import app as microk3_app

    class FakeRosManager:
        running = True

    microk3_app.ROS_AVAILABLE = True
    microk3_app.ros_manager = FakeRosManager()

    response = client.post(
        '/api/ros/pointcloud/accumulate/start',
        json={'topic_name': '/cloud', 'voxel_size': 0},
    )
    assert response.status_code == 400
    assert 'voxel_size' in response.get_json()['error']


def test_occupancy_map_api_with_fake_manager(client):
    import app as microk3_app

    class FakeRosManager:
        running = True

        def __init__(self):
            self.subscribed = []
            self.unsubscribed = []

        def start_occupancy_map(self, topic_name):
            self.subscribed.append(topic_name)
            return {'success': True, 'topic_name': topic_name}

        def stop_occupancy_map(self, topic_name):
            self.unsubscribed.append(topic_name)
            return {'success': True, 'topic_name': topic_name}

        def get_occupancy_map_snapshot(self, topic_name):
            assert topic_name == '/map'
            return {
                'width': 2,
                'height': 1,
                'resolution': 0.05,
                'origin': {'x': -1.0, 'y': 2.0, 'z': 0.0},
                'frame_id': 'map',
                'data_base64': b64encode(np.array([-1, 100], dtype=np.int8).tobytes()).decode('ascii'),
                'frames_received': 4,
                'last_error': None,
            }

    manager = FakeRosManager()
    microk3_app.ROS_AVAILABLE = True
    microk3_app.ros_manager = manager

    subscribe = client.post('/api/ros/occupancy_map/subscribe', json={})
    assert subscribe.status_code == 200
    assert manager.subscribed == ['/map']

    latest = client.get('/api/ros/occupancy_map/map/latest')
    assert latest.status_code == 200
    payload = latest.get_json()
    assert payload['width'] == 2
    assert payload['height'] == 1
    assert payload['data_base64']
    assert payload['frames_received'] == 4

    unsubscribe = client.post('/api/ros/occupancy_map/unsubscribe', json={'topic_name': 'map'})
    assert unsubscribe.status_code == 200
    assert manager.unsubscribed == ['/map']


def test_performance_metrics_update_exposed_by_api(client):
    import app as microk3_app

    microk3_app.system_data['nodes'].append(
        microk3_app.Node(
            id=755,
            name="Node 755",
            status="active",
            type="STM32",
            ram="Unknown",
            flash="Unknown",
            cpu="Unknown"
        )
    )

    microk3_app.ros_update_callback('performance_metrics', {
        'node_id': 755,
        'timestamp': '2026-04-01T10:00:00Z',
        'aggregate': {
            'client_to_agent_ms': 3.2,
            'client_to_agent_jitter_ms': 0.4,
            'agent_to_ros_ms': 1.3,
            'agent_to_ros_jitter_ms': 0.2,
            'end_to_end_ms': 4.5,
            'end_to_end_jitter_ms': 0.7,
            'lag_ms': 4.5,
            'jitter_ms': 0.7,
            'raw_delta_ms': 5.1,
            'bandwidth_bps': 2048.0,
            'rate_hz': 4.0,
            'sync_ready': True,
            'clock_offset_ms': 12.345,
            'clock_scale': 1.001,
            'time_sync_rtt_ms': 0.456,
            'time_sync_rtt_jitter_ms': 0.123,
            'time_sync_samples': 5,
        },
        'topics': {
            'heartbeat': {
                'client_to_agent_ms': 3.2,
                'client_to_agent_jitter_ms': 0.4,
                'agent_to_ros_ms': 1.3,
                'agent_to_ros_jitter_ms': 0.2,
                'end_to_end_ms': 4.5,
                'end_to_end_jitter_ms': 0.7,
                'lag_ms': 4.5,
                'raw_delta_ms': 5.1,
                'jitter_ms': 0.7,
                'bandwidth_bps': 1024.0,
                'rate_hz': 2.0,
            }
        }
    })
    microk3_app.ros_update_callback('container_metrics', {
        'timestamp': '2026-04-01T10:00:01Z',
        'aggregate': {
            'cpu_percent': 12.5,
            'memory_usage_bytes': 31457280,
            'memory_percent': 15.5,
            'rx_bps': 1200.0,
            'tx_bps': 900.0,
        },
        'containers': {
            'microk3': {
                'service': 'microk3',
                'cpu_percent': 12.5,
                'memory_usage_bytes': 31457280,
                'memory_percent': 15.5,
                'rx_bps': 1200.0,
                'tx_bps': 900.0,
            }
        }
    })

    response = client.get('/api/metrics/summary')
    assert response.status_code == 200
    summary = json.loads(response.data)
    assert summary['aggregate']['client_to_agent_ms'] == 3.2
    assert summary['aggregate']['agent_to_ros_ms'] == 1.3
    assert summary['aggregate']['end_to_end_ms'] == 4.5
    assert summary['aggregate']['lag_ms'] == 4.5
    assert summary['aggregate']['sync_ready'] is True
    assert summary['aggregate']['clock_offset_ms'] == 12.345
    assert summary['aggregate']['clock_scale'] == 1.001
    assert summary['aggregate']['time_sync_rtt_jitter_ms'] == 0.123
    assert summary['aggregate']['cpu_percent'] == 12.5
    assert summary['containers']['microk3']['memory_percent'] == 15.5

    node_response = client.get('/api/metrics/nodes/755')
    assert node_response.status_code == 200
    node_data = json.loads(node_response.data)
    assert node_data['aggregate']['bandwidth_bps'] == 2048.0
    assert "C->A 3.200 ms" in microk3_app.system_data['nodes'][0].network
    assert "A->ROS 1.300 ms" in microk3_app.system_data['nodes'][0].network
    assert "E2E 4.500 ms" in microk3_app.system_data['nodes'][0].network
    assert "stack" in microk3_app.system_data['nodes'][0].cpu


def test_performance_metrics_with_unknown_lag_keeps_node_renderable(client):
    import app as microk3_app

    microk3_app.system_data['nodes'].append(
        microk3_app.Node(
            id=755,
            name="Node 755",
            status="active",
            type="STM32",
            ram="Unknown",
            flash="Unknown",
            cpu="Unknown"
        )
    )

    microk3_app.ros_update_callback('performance_metrics', {
        'node_id': 755,
        'timestamp': '2026-04-01T10:00:00Z',
        'aggregate': {
            'client_to_agent_ms': None,
            'client_to_agent_jitter_ms': 0.4,
            'agent_to_ros_ms': 1.3,
            'agent_to_ros_jitter_ms': 0.2,
            'end_to_end_ms': None,
            'end_to_end_jitter_ms': 0.7,
            'lag_ms': None,
            'jitter_ms': 0.7,
            'raw_delta_ms': 5.1,
            'bandwidth_bps': 2048.0,
            'rate_hz': 4.0,
            'sync_ready': True,
            'clock_offset_ms': 12.345,
            'time_sync_rtt_ms': 0.456,
            'time_sync_rtt_jitter_ms': 0.123,
            'time_sync_samples': 5,
        },
        'topics': {
            'heartbeat': {
                'client_to_agent_ms': None,
                'client_to_agent_jitter_ms': 0.4,
                'agent_to_ros_ms': 1.3,
                'agent_to_ros_jitter_ms': 0.2,
                'end_to_end_ms': None,
                'end_to_end_jitter_ms': 0.7,
                'lag_ms': None,
                'raw_delta_ms': 5.1,
                'jitter_ms': 0.7,
                'bandwidth_bps': 1024.0,
                'rate_hz': 2.0,
            }
        }
    })

    response = client.get('/api/metrics/nodes/755')
    assert response.status_code == 200
    assert "C->A unknown" in microk3_app.system_data['nodes'][0].network
    assert "A->ROS 1.300 ms" in microk3_app.system_data['nodes'][0].network
    assert "E2E unknown" in microk3_app.system_data['nodes'][0].network
