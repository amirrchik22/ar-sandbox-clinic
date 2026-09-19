#version 330
// Привязка к проектору: точка датчика (uv) → пиксель проектора через гомографию.
in vec2 in_pos;          // -1..1
uniform mat3 u_warp;     // из профиля калибровки
out vec2 v_uv;
void main() {
    vec3 p = u_warp * vec3(in_pos, 1.0);
    gl_Position = vec4(p.xy / p.z, 0.0, 1.0);
    v_uv = in_pos * 0.5 + 0.5;
}
