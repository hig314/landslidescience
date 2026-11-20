/* Flow routing for landform.py: priority-flood fill with a small gradient
 * (Barnes et al. 2014), D8 steepest descent on the filled surface, and
 * drainage area. Cells with seed[i] != 0 (window edge, sea) are outlets.
 *
 *   cc -O2 -shared -fPIC -o _flow_core.so flow_core.c
 */
#include <stdlib.h>
#include <math.h>

typedef struct { double z; int i; } item;

static void push(item *h, int *n, item v) {
    int k = (*n)++;
    while (k > 0) {
        int p = (k - 1) / 2;
        if (h[p].z <= v.z) break;
        h[k] = h[p]; k = p;
    }
    h[k] = v;
}

static item pop(item *h, int *n) {
    item top = h[0], v = h[--(*n)];
    int k = 0;
    for (;;) {
        int c = 2 * k + 1;
        if (c >= *n) break;
        if (c + 1 < *n && h[c + 1].z < h[c].z) c++;
        if (v.z <= h[c].z) break;
        h[k] = h[c]; k = c;
    }
    h[k] = v;
    return top;
}

/* z: ny*nx heights. seed: outlets. fill: filled heights (out). rcv: receiver
 * index, -1 at outlets (out). area: contributing cells, itself included (out).
 * Returns 0, or 1 if memory ran out. */
int flow(const float *z, const unsigned char *seed, int ny, int nx, double eps,
         double *fill, int *rcv, double *area) {
    static const int dy[8] = {-1, -1, -1, 0, 0, 1, 1, 1}, dx[8] = {-1, 0, 1, -1, 1, -1, 0, 1};
    int n = ny * nx, nh = 0, k = 0;
    item *h = malloc(sizeof(item) * (size_t)n);
    int *order = malloc(sizeof(int) * (size_t)n);
    unsigned char *closed = calloc((size_t)n, 1);
    if (!h || !order || !closed) { free(h); free(order); free(closed); return 1; }
    for (int i = 0; i < n; i++) {
        int y = i / nx, x = i % nx;
        if (seed[i] || y == 0 || x == 0 || y == ny - 1 || x == nx - 1) {
            fill[i] = z[i]; closed[i] = 1;
            push(h, &nh, (item){z[i], i});
        }
        rcv[i] = -1; area[i] = 1.0;
    }
    while (nh) {
        item c = pop(h, &nh);
        int y = c.i / nx, x = c.i % nx;
        order[k++] = c.i;
        for (int d = 0; d < 8; d++) {
            int yy = y + dy[d], xx = x + dx[d];
            if (yy < 0 || xx < 0 || yy >= ny || xx >= nx) continue;
            int j = yy * nx + xx;
            if (closed[j]) continue;
            closed[j] = 1;
            fill[j] = z[j] > c.z + eps ? z[j] : c.z + eps;
            push(h, &nh, (item){fill[j], j});
        }
    }
    for (int i = 0; i < n; i++) {
        int y = i / nx, x = i % nx;
        if (seed[i] || y == 0 || x == 0 || y == ny - 1 || x == nx - 1) continue;
        double best = 0; int bj = -1;
        for (int d = 0; d < 8; d++) {
            int j = (y + dy[d]) * nx + x + dx[d];
            double s = (fill[i] - fill[j]) / ((dy[d] && dx[d]) ? 1.41421356 : 1.0);
            if (s > best) { best = s; bj = j; }
        }
        rcv[i] = bj;
    }
    for (int q = k - 1; q >= 0; q--) {
        int i = order[q];
        if (rcv[i] >= 0) area[rcv[i]] += area[i];
    }
    free(h); free(order); free(closed);
    return 0;
}
