// rs_track: minimal headless tracker for the phase 0 benchmark.
//
// Reads N raw RGB24 frames (W x H) from stdin, puts the point/box prompt on
// frame 0, propagates through the remaining frames, and for every frame writes
// to stdout: W*H bytes of mask (0/255) followed by one float64 with the time
// the model spent on that frame in milliseconds.
//
// Usage:
//   rs_track --model m.ggml --width W --height H --frames N
//            [--cpu] [--threads T] [--img-size S]
//            [--pos x,y]... [--neg x,y]... [--box x0,y0,x1,y1]

#include "sam3.h"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

static bool read_exact(void * dst, size_t n) {
    uint8_t * p = (uint8_t *) dst;
    while (n > 0) {
        size_t r = fread(p, 1, n, stdin);
        if (r == 0) return false;
        p += r;
        n -= r;
    }
    return true;
}

static void write_result(const std::vector<uint8_t> & mask, double ms) {
    fwrite(mask.data(), 1, mask.size(), stdout);
    fwrite(&ms, sizeof(ms), 1, stdout);
    fflush(stdout);
}

static double now_ms() {
    using namespace std::chrono;
    return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
}

// Copy the first detection's mask (or zeros) into out.
static void take_mask(const sam3_result & r, std::vector<uint8_t> & out) {
    std::fill(out.begin(), out.end(), 0);
    if (r.detections.empty()) return;
    const auto & m = r.detections[0].mask;
    if (m.data.size() == out.size()) out = m.data;
}

int main(int argc, char ** argv) {
    sam3_params params;
    params.n_threads = 4;
    params.use_gpu   = true;
    int W = 0, H = 0, N = 0;
    sam3_pvs_params pvs;
    pvs.multimask = false;

    for (int i = 1; i < argc; i++) {
        std::string a = argv[i];
        auto next = [&]() -> const char * {
            if (i + 1 >= argc) { fprintf(stderr, "missing value for %s\n", a.c_str()); exit(2); }
            return argv[++i];
        };
        if      (a == "--model")    params.model_path = next();
        else if (a == "--cpu")      params.use_gpu = false;
        else if (a == "--threads")  params.n_threads = atoi(next());
        else if (a == "--img-size") params.encode_img_size = atoi(next());
        else if (a == "--width")    W = atoi(next());
        else if (a == "--height")   H = atoi(next());
        else if (a == "--frames")   N = atoi(next());
        else if (a == "--pos" || a == "--neg") {
            sam3_point p;
            if (sscanf(next(), "%f,%f", &p.x, &p.y) != 2) { fprintf(stderr, "bad point\n"); return 2; }
            (a == "--pos" ? pvs.pos_points : pvs.neg_points).push_back(p);
        }
        else if (a == "--box") {
            sam3_box b;
            if (sscanf(next(), "%f,%f,%f,%f", &b.x0, &b.y0, &b.x1, &b.y1) != 4) { fprintf(stderr, "bad box\n"); return 2; }
            pvs.box = b;
            pvs.use_box = true;
        }
        else { fprintf(stderr, "unknown arg %s\n", a.c_str()); return 2; }
    }
    if (params.model_path.empty() || W <= 0 || H <= 0 || N <= 0) {
        fprintf(stderr, "need --model, --width, --height, --frames\n");
        return 2;
    }

    auto model = sam3_load_model(params);
    if (!model) { fprintf(stderr, "load failed\n"); return 1; }
    auto state = sam3_create_state(*model, params);
    if (!state) { fprintf(stderr, "state failed\n"); return 1; }

    sam3_visual_track_params vtp;
    vtp.max_keep_alive = 1 << 30;  // never drop the object in the benchmark
    auto tracker = sam3_create_visual_tracker(*model, vtp);

    sam3_image frame;
    frame.width = W;
    frame.height = H;
    frame.channels = 3;
    frame.data.resize((size_t) W * H * 3);
    std::vector<uint8_t> mask((size_t) W * H);

    fprintf(stderr, "RS_READY\n");

    for (int f = 0; f < N; f++) {
        if (!read_exact(frame.data.data(), frame.data.size())) {
            fprintf(stderr, "stdin ended at frame %d\n", f);
            return 1;
        }
        double t0 = now_ms();
        if (f == 0) {
            // Encode frame 0 through the tracker so its frame counter advances,
            // then register the prompted object on it.
            sam3_propagate_frame(*tracker, *state, *model, frame);
            int id = sam3_tracker_add_instance(*tracker, *state, *model, pvs);
            if (id < 0) { fprintf(stderr, "add_instance failed\n"); return 1; }
            take_mask(sam3_segment_pvs(*state, *model, pvs), mask);
        } else {
            take_mask(sam3_propagate_frame(*tracker, *state, *model, frame), mask);
        }
        write_result(mask, now_ms() - t0);
    }
    return 0;
}
