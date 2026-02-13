import pytest
import json
from app import app, get_default_data
from base64 import b64encode


@pytest.fixture
def client():
    """Create test client"""
    app.config['TESTING'] = True
    app.config['RATELIMIT_ENABLED'] = False  # Disable rate limiting for tests
    
    with app.test_client() as client:
        yield client


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


def test_health_check(client):
    """Test health endpoint"""
    response = client.get('/health')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['status'] == 'healthy'


def test_api_nodes(client):
    """Test nodes API endpoint"""
    response = client.get('/api/nodes')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert isinstance(data, list)
    assert len(data) > 0


def test_api_system_status(client):
    """Test system status endpoint"""
    response = client.get('/api/system_status')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert 'status' in data
    assert 'nodes_online' in data


def test_api_node_detail(client):
    """Test node detail endpoint"""
    response = client.get('/api/nodes/1')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['id'] == 1


def test_api_node_not_found(client):
    """Test node not found"""
    response = client.get('/api/nodes/999')
    assert response.status_code == 404


def test_update_node_no_auth(client):
    """Test update node without authentication"""
    response = client.post('/api/update_node',
                          json={'node_id': 1, 'status': 'standby'},
                          content_type='application/json')
    assert response.status_code == 401


def test_update_node_with_auth(client, auth_headers):
    """Test update node with authentication"""
    response = client.post('/api/update_node',
                          headers=auth_headers,
                          json={'node_id': 1, 'status': 'standby'},
                          content_type='application/json')
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['success'] is True


def test_update_node_invalid_status(client, auth_headers):
    """Test update node with invalid status"""
    response = client.post('/api/update_node',
                          headers=auth_headers,
                          json={'node_id': 1, 'status': 'invalid'},
                          content_type='application/json')
    assert response.status_code == 400


def test_update_node_missing_field(client, auth_headers):
    """Test update node with missing field"""
    response = client.post('/api/update_node',
                          headers=auth_headers,
                          json={'status': 'active'},
                          content_type='application/json')
    assert response.status_code == 400


def test_add_failure_with_auth(client, auth_headers):
    """Test adding failure with authentication"""
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
