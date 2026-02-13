import pytest
from models.node import Node


def test_node_creation():
    """Test creating a valid node"""
    node = Node(
        id=1,
        name="Test Node",
        status="active",
        type="STM32H743VIT6",
        ram="1MB",
        flash="2MB",
        cpu="480MHz"
    )
    assert node.id == 1
    assert node.name == "Test Node"
    assert node.status == "active"


def test_node_invalid_status():
    """Test node with invalid status"""
    with pytest.raises(ValueError):
        Node(
            id=1,
            name="Test Node",
            status="invalid",
            type="STM32H743VIT6",
            ram="1MB",
            flash="2MB",
            cpu="480MHz"
        )


def test_node_invalid_health_score():
    """Test node with invalid health score"""
    with pytest.raises(ValueError):
        Node(
            id=1,
            name="Test Node",
            status="active",
            type="STM32H743VIT6",
            ram="1MB",
            flash="2MB",
            cpu="480MHz",
            health_score=150
        )


def test_node_is_healthy():
    """Test is_healthy property"""
    node = Node(
        id=1,
        name="Test Node",
        status="active",
        type="STM32H743VIT6",
        ram="1MB",
        flash="2MB",
        cpu="480MHz",
        health_score=80
    )
    assert node.is_healthy is True
    
    node.health_score = 60
    assert node.is_healthy is False


def test_node_to_dict():
    """Test converting node to dictionary"""
    node = Node(
        id=1,
        name="Test Node",
        status="active",
        type="STM32H743VIT6",
        ram="1MB",
        flash="2MB",
        cpu="480MHz"
    )
    data = node.to_dict()
    assert isinstance(data, dict)
    assert data['id'] == 1
    assert data['name'] == "Test Node"


def test_node_from_dict():
    """Test creating node from dictionary"""
    data = {
        'id': 1,
        'name': 'Test Node',
        'status': 'active',
        'type': 'STM32H743VIT6',
        'ram': '1MB',
        'flash': '2MB',
        'cpu': '480MHz'
    }
    node = Node.from_dict(data)
    assert node.id == 1
    assert node.name == "Test Node"


def test_node_update_status():
    """Test updating node status"""
    node = Node(
        id=1,
        name="Test Node",
        status="active",
        type="STM32H743VIT6",
        ram="1MB",
        flash="2MB",
        cpu="480MHz"
    )
    node.update_status("standby")
    assert node.status == "standby"
    
    with pytest.raises(ValueError):
        node.update_status("invalid")
