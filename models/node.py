from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any
from datetime import datetime


@dataclass
class Node:
    """Represents a microcontroller node in the distributed system"""
    
    id: int
    name: str
    status: str
    type: str
    ram: str
    flash: str
    cpu: str
    active_tasks: List[str] = field(default_factory=list)
    health_score: int = 100
    uptime: str = "0h 0m"
    network: str = "CAN + Ethernet"
    
    # Valid values
    VALID_STATUSES = ['active', 'standby', 'offline']
    
    def __post_init__(self):
        """Validate node data after initialization"""
        if self.status not in self.VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{self.status}'. Must be one of {self.VALID_STATUSES}"
            )
        
        if not isinstance(self.health_score, int) or not 0 <= self.health_score <= 100:
            raise ValueError("Health score must be an integer between 0 and 100")
        
        if not isinstance(self.active_tasks, list):
            raise ValueError("active_tasks must be a list")
    
    @property
    def is_healthy(self) -> bool:
        """Check if node health is acceptable"""
        return self.health_score >= 70 and self.status == 'active'
    
    @property
    def is_active(self) -> bool:
        """Check if node is active"""
        return self.status == 'active'
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Node':
        """Create Node instance from dictionary"""
        return cls(**data)
    
    def update_status(self, new_status: str) -> None:
        """Update node status with validation"""
        if new_status not in self.VALID_STATUSES:
            raise ValueError(
                f"Invalid status '{new_status}'. Must be one of {self.VALID_STATUSES}"
            )
        self.status = new_status
    
    def update_health(self, new_score: int) -> None:
        """Update health score with validation"""
        if not isinstance(new_score, int) or not 0 <= new_score <= 100:
            raise ValueError("Health score must be an integer between 0 and 100")
        self.health_score = new_score
    
    def add_task(self, task_name: str) -> None:
        """Add a task to this node"""
        if task_name not in self.active_tasks:
            self.active_tasks.append(task_name)
    
    def remove_task(self, task_name: str) -> None:
        """Remove a task from this node"""
        if task_name in self.active_tasks:
            self.active_tasks.remove(task_name)
    
    def __repr__(self) -> str:
        return (
            f"Node(id={self.id}, name='{self.name}', status='{self.status}', "
            f"health={self.health_score}, tasks={len(self.active_tasks)})"
        )
