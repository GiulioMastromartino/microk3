(function () {
    function isOpticalFrame(frameId) {
        return String(frameId || '').toLowerCase().includes('optical');
    }

    function remapRosToThree(x, y, z, frameId) {
        if (isOpticalFrame(frameId)) {
            return [x, -y, -z];
        }
        return [-y, z, -x];
    }

    function remapPositionsRosToThree(positions, frameId) {
        for (let index = 0; index < positions.length; index += 3) {
            const [x, y, z] = remapRosToThree(
                positions[index], positions[index + 1], positions[index + 2], frameId
            );
            positions[index] = x;
            positions[index + 1] = y;
            positions[index + 2] = z;
        }
        return positions;
    }

    function coordinateConvention(frameId) {
        return isOpticalFrame(frameId) ? 'optical: x, -y, -z' : 'body REP-103: -y, z, -x';
    }

    window.MicroK3RosThree = {
        remapRosToThree,
        remapPositionsRosToThree,
        coordinateConvention,
    };
}());