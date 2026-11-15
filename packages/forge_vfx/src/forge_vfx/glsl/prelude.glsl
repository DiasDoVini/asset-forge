// Asset Forge VFX prelude: helpers available to every effect (portable GLSL subset, also translated to HLSL).
// Conventions: uv in 0..1 with origin at the bottom-left; uTime in seconds; uProgress in 0..1 (one-shot effects);
// sampleTex(uv) reads the input sprite (transparent when the effect has no texture).

float hash11(float p) {
    p = fract(p * 0.1031);
    p *= p + 33.33;
    p *= p + p;
    return fract(p);
}

float hash21(vec2 p) {
    vec3 p3 = fract(vec3(p.x, p.y, p.x) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

vec2 hash22(vec2 p) {
    vec3 p3 = fract(vec3(p.x, p.y, p.x) * vec3(0.1031, 0.1030, 0.0973));
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.xx + p3.yz) * p3.zy);
}

float vnoise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    float a = hash21(i);
    float b = hash21(i + vec2(1.0, 0.0));
    float c = hash21(i + vec2(0.0, 1.0));
    float d = hash21(i + vec2(1.0, 1.0));
    return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

float fbm(vec2 p) {
    float v = 0.0;
    float a = 0.5;
    for (int i = 0; i < 5; i++) {
        v += a * vnoise(p);
        p = p * 2.03 + vec2(17.1, 9.2);
        a *= 0.5;
    }
    return v;
}

// distance to the nearest cell point (cellular noise)
float voronoi(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    float md = 8.0;
    for (int y = -1; y <= 1; y++) {
        for (int x = -1; x <= 1; x++) {
            vec2 g = vec2(float(x), float(y));
            vec2 o = hash22(i + g);
            md = min(md, length(g + o - f));
        }
    }
    return md;
}

vec2 rot2(vec2 p, float a) {
    float c = cos(a);
    float s = sin(a);
    return vec2(c * p.x - s * p.y, s * p.x + c * p.y);
}

// polar coordinates around the center: (radius, angle in -PI..PI)
vec2 polar(vec2 uv) {
    vec2 p = uv - 0.5;
    return vec2(length(p), atan(p.y, p.x));
}

vec3 hsv2rgb(vec3 c) {
    vec3 p = abs(fract(vec3(c.x, c.x, c.x) + vec3(1.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - 3.0);
    return c.z * mix(vec3(1.0, 1.0, 1.0), clamp(p - 1.0, 0.0, 1.0), c.y);
}

float sdCircle(vec2 p, float r) {
    return length(p) - r;
}

float sdBox(vec2 p, vec2 b) {
    vec2 d = abs(p) - b;
    return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0);
}

float sdRing(vec2 p, float r, float w) {
    return abs(length(p) - r) - w;
}

// soft glow falloff for a signed distance d (bigger radius = wider glow)
float glow(float d, float radius) {
    return radius / (radius + max(d, 0.0) * max(d, 0.0) * 400.0);
}

float remap(float v, float a, float b, float c, float d) {
    return c + (d - c) * clamp((v - a) / (b - a), 0.0, 1.0);
}

// premultiplies nothing: returns straight alpha color from rgb + intensity (handy for additive effects)
vec4 emissive(vec3 rgb, float intensity) {
    float a = clamp(intensity, 0.0, 1.0);
    return vec4(rgb * max(intensity, 0.0) / max(a, 0.0001), a);
}
