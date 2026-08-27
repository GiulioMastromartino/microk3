(function () {
    function isOpticalFrame(frameId) {
        return String(frameId || '').toLowerCase().includes('optical');
    }

    function isZedLeftCameraFrame(frameId) {
        return String(frameId || '').toLowerCase() === 'zed_left_camera_frame';
    }

    function levelZedLeftCamera(x, y, z) {
        // Calibration from the dominant floor plane in zed_left_camera_frame.
        const pitch = 9.88 * Math.PI / 180;
        const roll = 9.68 * Math.PI / 180;
        const cosPitch = Math.cos(pitch);
        const sinPitch = Math.sin(pitch);
        const cosRoll = Math.cos(roll);
        const sinRoll = Math.sin(roll);
        const leveledX = cosPitch * x + sinPitch * z;
        const leveledZ = -sinPitch * x + cosPitch * z;
        return [leveledX, cosRoll * y - sinRoll * leveledZ, sinRoll * y + cosRoll * leveledZ];
    }

    function remapRosToThree(x, y, z, frameId) {
        if (isZedLeftCameraFrame(frameId)) {
            [x, y, z] = levelZedLeftCamera(x, y, z);
        }
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
        if (isZedLeftCameraFrame(frameId)) {
            return 'ZED base: leveled -y, z, -x';
        }
        return isOpticalFrame(frameId) ? 'optical: x, -y, -z' : 'body REP-103: -y, z, -x';
    }

    window.MicroK3RosThree = {
        remapRosToThree,
        remapPositionsRosToThree,
        levelZedLeftCamera,
        coordinateConvention,
    };
}());