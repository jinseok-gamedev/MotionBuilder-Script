"""Math utilities for T-Pose alignment.

Pure-Python (no numpy) helpers for the rotation math needed to compute
non-destructive offsets that take a bone from its current world orientation
to a desired canonical orientation.

All angles are in degrees unless explicitly suffixed ``_rad``. Quaternions
are stored as ``(w, x, y, z)`` tuples. ``Mat4`` is a flat 16-tuple in the
same row-major layout MotionBuilder uses for ``FBMatrix`` (translation in
elements ``[12], [13], [14]``).

The module deliberately avoids importing ``pyfbsdk`` at module load so it
can be unit tested outside MotionBuilder; ``FBRVector`` conversion is in a
lazy helper at the bottom.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence, Tuple

Vec3 = Tuple[float, float, float]
Vec4 = Tuple[float, float, float, float]
Quat = Tuple[float, float, float, float]
Mat4 = Tuple[
    float, float, float, float,
    float, float, float, float,
    float, float, float, float,
    float, float, float, float,
]

EPSILON = 1e-8


def fb_matrix_to_tuple(fb_matrix) -> Mat4:
    """Convert ``FBMatrix`` to a flat 16-tuple."""
    return tuple(float(fb_matrix[i]) for i in range(16))  # type: ignore[return-value]


def extract_basis(m: Mat4) -> Tuple[Vec3, Vec3, Vec3]:
    """Return the three orthonormal column basis vectors of a 4x4 matrix.

    Non-uniform scale is removed via per-axis normalization.
    """
    x = (m[0], m[1], m[2])
    y = (m[4], m[5], m[6])
    z = (m[8], m[9], m[10])
    return (vec_normalize(x), vec_normalize(y), vec_normalize(z))


def matrix_translation(m: Mat4) -> Vec3:
    return (m[12], m[13], m[14])


def vec_sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vec_add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vec_scale(v: Vec3, s: float) -> Vec3:
    return (v[0] * s, v[1] * s, v[2] * s)


def vec_dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vec_cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def vec_length(v: Vec3) -> float:
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def vec_normalize(v: Vec3) -> Vec3:
    n = vec_length(v)
    if n < EPSILON:
        return (0.0, 0.0, 0.0)
    return (v[0] / n, v[1] / n, v[2] / n)


def quat_identity() -> Quat:
    return (1.0, 0.0, 0.0, 0.0)


def quat_from_axis_angle(axis: Vec3, angle_rad: float) -> Quat:
    a = vec_normalize(axis)
    if vec_length(a) < EPSILON:
        return quat_identity()
    half = angle_rad * 0.5
    s = math.sin(half)
    return (math.cos(half), a[0] * s, a[1] * s, a[2] * s)


def quat_normalize(q: Quat) -> Quat:
    n = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if n < EPSILON:
        return quat_identity()
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def quat_conjugate(q: Quat) -> Quat:
    return (q[0], -q[1], -q[2], -q[3])


def quat_mul(a: Quat, b: Quat) -> Quat:
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def quat_angle_rad(q: Quat) -> float:
    """Rotation magnitude in radians, in [0, pi]."""
    w = max(-1.0, min(1.0, q[0]))
    return 2.0 * math.acos(abs(w))


def quat_from_basis(x_axis: Vec3, y_axis: Vec3, z_axis: Vec3) -> Quat:
    """Build a quaternion from three orthonormal column basis vectors using
    Shepperd's method for numerical stability.
    """
    m00, m01, m02 = x_axis[0], y_axis[0], z_axis[0]
    m10, m11, m12 = x_axis[1], y_axis[1], z_axis[1]
    m20, m21, m22 = x_axis[2], y_axis[2], z_axis[2]
    trace = m00 + m11 + m22

    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (m21 - m12) / s
        y = (m02 - m20) / s
        z = (m10 - m01) / s
    elif (m00 > m11) and (m00 > m22):
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        w = (m21 - m12) / s
        x = 0.25 * s
        y = (m01 + m10) / s
        z = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        w = (m02 - m20) / s
        x = (m01 + m10) / s
        y = 0.25 * s
        z = (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        w = (m10 - m01) / s
        x = (m02 + m20) / s
        y = (m12 + m21) / s
        z = 0.25 * s

    return quat_normalize((w, x, y, z))


def quat_from_matrix(m: Mat4) -> Quat:
    x_axis, y_axis, z_axis = extract_basis(m)
    return quat_from_basis(x_axis, y_axis, z_axis)


def quat_to_euler_xyz_deg(q: Quat) -> Vec3:
    """Convert a quaternion to XYZ Euler angles in degrees.

    Uses ``Rx * Ry * Rz`` order which matches MotionBuilder's default
    ``FBRotationOrder.kFBXYZ``. At the gimbal singularity (``ry = +-90``),
    ``rz`` is zeroed and folded into ``rx``.
    """
    q = quat_normalize(q)
    w, x, y, z = q

    sin_y = 2.0 * (w * y - z * x)
    sin_y = max(-1.0, min(1.0, sin_y))

    if abs(sin_y) > 0.99999:
        ry = math.copysign(math.pi / 2.0, sin_y)
        rx = math.atan2(-2.0 * (y * z - w * x), 1.0 - 2.0 * (x * x + y * y))
        rz = 0.0
    else:
        ry = math.asin(sin_y)
        rx = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
        rz = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))

    return (math.degrees(rx), math.degrees(ry), math.degrees(rz))


def quat_relative(current: Quat, target: Quat) -> Quat:
    """Quaternion ``q_offset`` such that ``q_offset * current == target``.

    Equivalent to ``target * inverse(current)``.
    """
    return quat_normalize(quat_mul(target, quat_conjugate(current)))


def shortest_equivalent(q: Quat) -> Quat:
    """Return whichever of ``q`` or ``-q`` represents the smaller rotation.

    Quaternions ``q`` and ``-q`` describe the same orientation but Euler
    decomposition can land in the larger of the two equivalent rotations,
    causing a visible 180-degree flip when applied as an offset.
    """
    if q[0] < 0.0:
        return (-q[0], -q[1], -q[2], -q[3])
    return q


def angle_between_vectors_rad(a: Vec3, b: Vec3) -> float:
    a = vec_normalize(a)
    b = vec_normalize(b)
    d = max(-1.0, min(1.0, vec_dot(a, b)))
    return math.acos(d)


def quat_from_two_vectors(src: Vec3, dst: Vec3) -> Quat:
    """Quaternion that rotates the ``src`` direction onto ``dst``."""
    a = vec_normalize(src)
    b = vec_normalize(dst)
    if vec_length(a) < EPSILON or vec_length(b) < EPSILON:
        return quat_identity()

    d = max(-1.0, min(1.0, vec_dot(a, b)))
    if d > 1.0 - EPSILON:
        return quat_identity()
    if d < -1.0 + EPSILON:
        ortho = vec_cross(a, (1.0, 0.0, 0.0))
        if vec_length(ortho) < EPSILON:
            ortho = vec_cross(a, (0.0, 1.0, 0.0))
        return quat_from_axis_angle(vec_normalize(ortho), math.pi)

    axis = vec_cross(a, b)
    angle = math.acos(d)
    return quat_from_axis_angle(axis, angle)


def look_rotation_quat(forward: Vec3, up_hint: Vec3 = (0.0, 1.0, 0.0)) -> Quat:
    """Build a quaternion whose local +Z axis points along ``forward`` and
    whose local +Y axis lies in the plane defined by ``forward`` and
    ``up_hint``.
    """
    f = vec_normalize(forward)
    if vec_length(f) < EPSILON:
        return quat_identity()
    u = vec_normalize(up_hint)
    if vec_length(u) < EPSILON:
        u = (0.0, 1.0, 0.0)
    if abs(vec_dot(f, u)) > 1.0 - EPSILON:
        u = (0.0, 0.0, 1.0) if abs(f[1]) > 0.9 else (0.0, 1.0, 0.0)
    right = vec_normalize(vec_cross(u, f))
    new_up = vec_cross(f, right)
    return quat_from_basis(right, new_up, f)


def degrees_magnitude(q: Quat) -> float:
    """Rotation magnitude of a quaternion in degrees."""
    return math.degrees(quat_angle_rad(q))


def vec3_from_fb(fb_vec) -> Vec3:
    return (float(fb_vec[0]), float(fb_vec[1]), float(fb_vec[2]))


def euler_to_fb_rvector(euler_deg: Vec3):
    """Convert an Euler tuple into ``FBRVector`` (lazy, version-tolerant import)."""
    from ._compat import FBRVector
    return FBRVector(float(euler_deg[0]), float(euler_deg[1]), float(euler_deg[2]))


def iterable_close(a: Iterable[float], b: Iterable[float], tol: float = 1e-4) -> bool:
    a = list(a)
    b = list(b)
    if len(a) != len(b):
        return False
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def project_vector_onto_plane(v: Vec3, plane_normal: Vec3) -> Vec3:
    n = vec_normalize(plane_normal)
    d = vec_dot(v, n)
    return vec_sub(v, vec_scale(n, d))


def basis_from_forward_up(forward: Vec3, up_hint: Vec3) -> Tuple[Vec3, Vec3, Vec3]:
    """Return an orthonormal basis ``(x, y, z)`` with ``z = forward``."""
    z = vec_normalize(forward)
    if vec_length(z) < EPSILON:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    up = vec_normalize(up_hint)
    if vec_length(up) < EPSILON:
        up = (0.0, 1.0, 0.0)
    if abs(vec_dot(z, up)) > 1.0 - EPSILON:
        up = (0.0, 0.0, 1.0) if abs(z[1]) > 0.9 else (0.0, 1.0, 0.0)
    x = vec_normalize(vec_cross(up, z))
    y = vec_cross(z, x)
    return (x, y, z)


def slerp(a: Quat, b: Quat, t: float) -> Quat:
    """Spherical linear interpolation between two unit quaternions."""
    a = quat_normalize(a)
    b = quat_normalize(b)
    dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
    if dot < 0.0:
        b = (-b[0], -b[1], -b[2], -b[3])
        dot = -dot
    if dot > 0.9995:
        result = (
            a[0] + t * (b[0] - a[0]),
            a[1] + t * (b[1] - a[1]),
            a[2] + t * (b[2] - a[2]),
            a[3] + t * (b[3] - a[3]),
        )
        return quat_normalize(result)
    theta_0 = math.acos(dot)
    sin_theta_0 = math.sin(theta_0)
    theta = theta_0 * t
    s0 = math.cos(theta) - dot * math.sin(theta) / sin_theta_0
    s1 = math.sin(theta) / sin_theta_0
    return (
        s0 * a[0] + s1 * b[0],
        s0 * a[1] + s1 * b[1],
        s0 * a[2] + s1 * b[2],
        s0 * a[3] + s1 * b[3],
    )
