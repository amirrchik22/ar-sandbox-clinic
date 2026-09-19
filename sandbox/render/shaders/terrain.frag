#version 330
// Высота → цвет палитры + горизонтали + режим рук.
uniform sampler2D u_height;    // R32F, мм над дном
uniform sampler2D u_hand;      // R8: 1 = рука
uniform sampler2D u_palette;   // 256×1 RGB
uniform float u_hmin;          // мм
uniform float u_hmax;          // мм
uniform float u_contour_mm;    // шаг горизонталей
uniform float u_hand_mode;     // 0 = красить руки, 1 = не красить (нейтральный свет)
uniform vec3  u_hand_color;    // цвет света на руках в режиме 1
in vec2 v_uv;
out vec4 f_color;

void main() {
    float h = texture(u_height, v_uv).r;
    float t = clamp((h - u_hmin) / (u_hmax - u_hmin), 0.0, 1.0);
    vec3 c = texture(u_palette, vec2(t, 0.5)).rgb;

    // горизонтали: тонкая тёмная линия на каждом кратном u_contour_mm, без мерцания
    float k = h / u_contour_mm;
    float d = abs(fract(k + 0.5) - 0.5) / max(fwidth(k), 1e-4);
    float line = 1.0 - smoothstep(0.6, 1.4, d);
    c = mix(c, c * 0.55, line);

    if (u_hand_mode > 0.5 && texture(u_hand, v_uv).r > 0.5) {
        c = u_hand_color;
    }
    f_color = vec4(c, 1.0);
}
