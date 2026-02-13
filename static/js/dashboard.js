// Refresh button functionality
document.getElementById('refreshBtn').addEventListener('click', function() {
    location.reload();
});

// Initialize charts when the page loads
document.addEventListener('DOMContentLoaded', function() {
    // Resource Usage Chart (this would be dynamically generated in a real app)
    const resourceCtx = document.getElementById('resourceChart').getContext('2d');
    
    // Network Traffic Chart (this would be dynamically generated in a real app)
    const networkCtx = document.getElementById('networkChart').getContext('2d');
    
    // Simulate loading data
    setTimeout(function() {
        // Resource usage chart initialization (would normally come from API)
        const resourceChart = new Chart(resourceCtx, {
            type: 'bar',
            data: {
                labels: ['CPU Usage', 'Memory Usage', 'Network Usage'],
                datasets: [{
                    label: 'Usage %',
                    data: [65, 42, 30],
                    backgroundColor: [
                        'rgba(52, 152, 219, 0.7)',
                        'rgba(46, 204, 113, 0.7)',
                        'rgba(241, 196, 15, 0.7)'
                    ],
                    borderColor: [
                        'rgba(52, 152, 219, 1)',
                        'rgba(46, 204, 113, 1)',
                        'rgba(241, 196, 15, 1)'
                    ],
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100
                    }
                }
            }
        });
        
        // Network traffic chart initialization (would normally come from API)
        const networkChart = new Chart(networkCtx, {
            type: 'line',
            data: {
                labels: ['0s', '10s', '20s', '30s', '40s', '50s', '60s'],
                datasets: [{
                    label: 'Traffic (Mbps)',
                    data: [2.4, 3.1, 2.8, 4.2, 3.7, 2.9, 3.5],
                    fill: true,
                    backgroundColor: 'rgba(231, 76, 60, 0.2)',
                    borderColor: 'rgba(231, 76, 60, 1)',
                    tension: 0.3
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true
                    }
                }
            }
        });
    }, 500);
});

// Simulate API calls for dashboard data
function refreshDashboardData() {
    // In a real application, this would fetch from the API endpoints
    console.log('Refreshing dashboard data...');
    
    // Simulate updating node statuses
    const nodes = document.querySelectorAll('.node-card');
    nodes.forEach(node => {
        // This is just a demonstration - in real app, this would come from API
        const statusIndicator = node.querySelector('.badge');
        if (statusIndicator) {
            statusIndicator.textContent = "Active";
        }
    });
}

// Set up periodic refresh (every 30 seconds in a real app)
setInterval(refreshDashboardData, 30000);
