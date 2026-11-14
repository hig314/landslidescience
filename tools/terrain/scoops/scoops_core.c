/* scoops_core.c -- 3D rotational limit equilibrium over a DEM.
 *
 * A rebuild of the USGS Scoops3D method (Reid, Christian, Brien & Henderson
 * 2015, Techniques and Methods 14-A1; v1.3 rules from its ReadMe), written from
 * the manual, not from their source. Equation numbers below are the manual's.
 * Dry, uniform material only: no pore pressure, earthquake or layers. Checked
 * against USGS's published runs by benchmark_usgs.py.
 *
 * What is added: the same trial surfaces also solve for the cohesion needed for
 * F = 1 at a list of friction angles, which needs no value of strength as input
 * and no iteration (see `required_c`). Each cell keeps the highest requirement of
 * any surface containing it, per friction angle and per volume class.
 *
 * Grid: dem[j*nx+i], j = 0 is the SOUTH row, x_i = (i+.5)dx, y_j = (j+.5)dx, in
 * a metric projection. Heights and lengths in metres, gamma in kN/m3, c in kPa.
 */
#include <math.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    int i, j, full;
    double v, ah, cx, cy;          /* volume, horizontal area, centroid */
} col_t;

typedef struct {
    double v, ah, area, weight, f_bishop, f_ordinary, delta_deg, sigma, tau;
    int ncols;
} single_t;

typedef struct {
    long long surfaces, columns, bishop_failed, req_filtered, wrapped;
} stats_t;

static const double *DEM;
static double *ZG, *ZX, *ZY;        /* corner heights, cell-centre gradients */
static int NX, NY;
static double DX;
static double *DEPTH;               /* scratch: corner depths, NAN = outside */
static int *LAB;                    /* scratch: subset label per column */
static col_t *COLS;
static int *STACK;

#define MALPHA_MIN 0.0

/* Optional extra maps for comparing how a surface's requirement is painted, for
 * the first friction angle and first volume class. All 0 where no surface with
 * c > 0 reached. The local demand of a column,
 *     c_i = (W/Ah)(sin a  m_a - tan phi)
 * is what the column would need alone: c is exactly the mean of c_i weighted by
 * R Ah / m_a, so columns that drive are positive and those that buttress negative. */
enum { XF_GATE,     /* highest c among surfaces whose base dips with slip here */
       XF_CSIN,     /* highest c sin(a), a the base dip along slip; a <= 0 gives 0 */
       XF_CGROUND,  /* highest c sin(ground fall along slip) */
       XF_LOCAL,    /* highest c_i among surfaces with c > 0 */
       XF_H_A,      /* column depth under the surface with the highest c */
       XF_HMAX_A,   /* deepest full column of that surface */
       XF_SINA_A,   /* sin(a) at this cell on that surface */
       XF_H_C,      /* column depth under the surface with the highest c sin(a) */
       XF_HMAX_C,   /* deepest full column of that surface */
       XF_H_ANY,    /* greatest column depth among all surfaces with c > 0 */
       XF_HMAX_ANY, /* greatest deepest-column among them */
       XF_H_WIN,    /* greatest column depth among surfaces holding the top c at some cell */
       XF_HMAX_WIN, /* greatest deepest-column among them */
       NXF };
static float *XF = NULL;            /* [NXF][ny][nx] or NULL */
static double XF_IDERR = 0;         /* worst |c - weighted mean of c_i| seen */
/* Second pass. A surface "wins" if it holds the final top c at any cell, which can
 * only be known once the map is finished: given that map (merged over every worker and
 * lattice), a rerun of the same search writes XF_H_WIN and XF_HMAX_WIN and nothing else. */
static const float *XF_FINAL = NULL;
void scoops_final_set(const float *f) { XF_FINAL = f; }
void scoops_extras_set(float *x) { XF = x; XF_IDERR = 0; }
double scoops_extras_iderr(void) { return XF_IDERR; }
int scoops_extras_n(void) { return NXF; }

/* The strength envelope. In the Ordinary method a surface's requirement is a straight
 * line in strength space, with no friction angle assumed:
 *     F = (c A + T tan phi) / D,  so at F = 1:  c = tau - sigma tan phi
 *     tau = D / A,  sigma = T / A      (kPa; A = Aterm, T = Tord: moment-weighted means)
 * For each surface in the first class whose volume centroid is in the box: a histogram
 * of (sigma, tau), and per spatial tile the highest tau per sigma bin, the highest
 * tau / sigma, and per friction angle the highest c with that surface's geometry.
 * Bishop is not linear in tan phi, so its c is kept beside the Ordinary one. */
#define ENV_NB 128
#define ENV_LO 1.0                  /* log10 kPa of the first bin edge */
#define ENV_PERDEC 32.0
#define ENV_NTOP 8                  /* c, xc, yc, zc, R, V, sigma, tau */
static struct {
    int on, nt;                     /* nt x nt tiles */
    double x0, y0, size;
    double *hist;                   /* [NB][NB]: sigma, tau */
    double *tile_tau;               /* [ntile][NB] */
    double *tile_ratio;             /* [ntile] */
    double *tile_cord;              /* [ntile][nphi] */
    double *tile_top;               /* [ntile][nphi][NTOP], Bishop */
    float *cell_ratio;              /* [ny][nx], highest tau / sigma of any surface reaching */
    int nphi;
} ENV;

void scoops_env_set(double x0, double y0, double size, int nt, int nphi, double *hist, double *tile_tau,
                    double *tile_ratio, double *tile_cord, double *tile_top, float *cell_ratio)
{
    ENV.on = 1; ENV.x0 = x0; ENV.y0 = y0; ENV.size = size; ENV.nt = nt; ENV.nphi = nphi;
    ENV.hist = hist; ENV.tile_tau = tile_tau; ENV.tile_ratio = tile_ratio;
    ENV.tile_cord = tile_cord; ENV.tile_top = tile_top; ENV.cell_ratio = cell_ratio;
}

static int env_bin(double v)
{
    if (v <= 0) return -1;
    int b = (int)floor((log10(v) - ENV_LO) * ENV_PERDEC);
    return b < 0 || b >= ENV_NB ? -1 : b;
}

void scoops_setup(const double *dem, int ny, int nx, double dx)
{
    DEM = dem; NY = ny; NX = nx; DX = dx;
    free(ZG); free(ZX); free(ZY); free(DEPTH); free(LAB); free(COLS); free(STACK);
    ZG = malloc(sizeof(double) * (ny + 1) * (nx + 1));
    ZX = malloc(sizeof(double) * ny * nx);
    ZY = malloc(sizeof(double) * ny * nx);
    DEPTH = malloc(sizeof(double) * (ny + 1) * (nx + 1));
    LAB = malloc(sizeof(int) * ny * nx);
    COLS = malloc(sizeof(col_t) * ny * nx);
    STACK = malloc(sizeof(int) * ny * nx);
    /* "Column corner elevations are computed as the average of the four
     * surrounding DEM cell elevations" (2.1) */
    for (int l = 0; l <= ny; l++)
        for (int k = 0; k <= nx; k++) {
            double s = 0; int n = 0;
            for (int b = l - 1; b <= l; b++)
                for (int a = k - 1; a <= k; a++)
                    if (a >= 0 && b >= 0 && a < nx && b < ny) { s += dem[b * nx + a]; n++; }
            ZG[l * (nx + 1) + k] = s / n;
        }
    for (int j = 0; j < ny; j++)
        for (int i = 0; i < nx; i++) {
            int a = i > 0 ? i - 1 : i, b = i < nx - 1 ? i + 1 : i;
            int c = j > 0 ? j - 1 : j, d = j < ny - 1 ? j + 1 : j;
            ZX[j * nx + i] = (dem[j * nx + b] - dem[j * nx + a]) / ((b - a) * dx);
            ZY[j * nx + i] = (dem[d * nx + i] - dem[c * nx + i]) / ((d - c) * dx);
        }
}

/* Volume of everything inside the sphere, by cell centres: only to find the
 * starting radius, so it need not be the exact column volume. */
static double quick_volume(double xc, double yc, double zc, double R)
{
    int i0 = (int)floor((xc - R) / DX), i1 = (int)ceil((xc + R) / DX);
    int j0 = (int)floor((yc - R) / DX), j1 = (int)ceil((yc + R) / DX);
    if (i0 < 0) i0 = 0;
    if (j0 < 0) j0 = 0;
    if (i1 > NX - 1) i1 = NX - 1;
    if (j1 > NY - 1) j1 = NY - 1;
    double v = 0, R2 = R * R;
    for (int j = j0; j <= j1; j++) {
        double dy = (j + .5) * DX - yc, dy2 = dy * dy;
        const double *row = DEM + j * NX;
        for (int i = i0; i <= i1; i++) {
            double dx = (i + .5) * DX - xc, r2 = dx * dx + dy2;
            if (r2 >= R2) continue;
            double d = row[i] - (zc - sqrt(R2 - r2));
            if (d > 0) v += d;
        }
    }
    return v * DX * DX;
}

/* One column's horizontal geometry and volume. Corners d[]: depth, NAN outside. */
static int column_geometry(col_t *c, double xc, double yc, double zc, double R)
{
    int i = c->i, j = c->j, w = NX + 1;
    double R2 = R * R;
    double d1 = DEPTH[j * w + i], d2 = DEPTH[j * w + i + 1];
    double d3 = DEPTH[(j + 1) * w + i + 1], d4 = DEPTH[(j + 1) * w + i];
    double z1 = ZG[j * w + i], z2 = ZG[j * w + i + 1], z3 = ZG[(j + 1) * w + i + 1], z4 = ZG[(j + 1) * w + i];
    double x0 = i * DX, y0 = j * DX;
    if (d1 > 0 && d2 > 0 && d3 > 0 && d4 > 0) {
        /* Prismoid (2.4) with the middle section from the sphere at the column
         * centre (v1.3). The centre's ground is the corners' mean. */
        double x = x0 + .5 * DX, y = y0 + .5 * DX, r2 = (x - xc) * (x - xc) + (y - yc) * (y - yc);
        double dc = .25 * (z1 + z2 + z3 + z4) - (zc - sqrt(R2 - r2));
        double s0 = .5 * (d1 + d4) * DX, s2 = .5 * (d2 + d3) * DX, s1 = dc * DX;
        c->v = DX / 6 * (s0 + 4 * s1 + s2);
        c->ah = DX * DX; c->cx = x; c->cy = y; c->full = 1;
        return c->v > 0;
    }
    /* Partial column: v1.3 includes one, two and three corner cases with
     * polygon formulas; here one rule for all, 6 x 6 sub-points. */
    enum { N = 6 };
    int n = 0; double sumd = 0, sx = 0, sy = 0;
    for (int b = 0; b < N; b++) {
        double fy = (b + .5) / N, y = y0 + fy * DX, dy = y - yc;
        for (int a = 0; a < N; a++) {
            double fx = (a + .5) / N, x = x0 + fx * DX, dx = x - xc;
            double r2 = dx * dx + dy * dy;
            if (r2 >= R2) continue;
            double g = z1 * (1 - fx) * (1 - fy) + z2 * fx * (1 - fy) + z3 * fx * fy + z4 * (1 - fx) * fy;
            double d = g - (zc - sqrt(R2 - r2));
            if (d <= 0) continue;
            n++; sumd += d; sx += x; sy += y;
        }
    }
    if (!n) return 0;
    double cell = DX * DX / (N * N);
    c->v = sumd * cell; c->ah = n * cell; c->cx = sx / n; c->cy = sy / n; c->full = 0;
    return 1;
}

/* Find the subsets of one sphere; for each valid one call back. Returns the
 * total volume; *touch is set if any subset reaches the DEM edge. */
typedef struct {
    double vmin, vmax, gamma;
    int minc, nphi, nbin;
    const double *tanphi, *edges;       /* edges: nbin+1 volumes */
    float *creq, *creq_vol;             /* [nphi][nbin][ny][nx] */
    float *creq_ord;                    /* same, Ordinary method, or NULL */
    double c, tanf;                     /* forward mode if c >= 0 */
    float *fmin, *fvol, *fmin_ord;      /* [ny][nx] */
    double mincellfrac;
    stats_t *st;
    single_t *single;                   /* single-surface mode */
    double force_delta;                 /* radians, or > 100 = use fall direction */
    double *node_best;                  /* highest c of any surface (may be < 0), first phi */
} run_t;

static double sphere(const run_t *rn, double xc, double yc, double zc, double R, int *touch)
{
    int w = NX + 1;
    double R2 = R * R;
    *touch = 0;
    int ci0 = (int)floor((xc - R) / DX), ci1 = (int)ceil((xc + R) / DX);
    int cj0 = (int)floor((yc - R) / DX), cj1 = (int)ceil((yc + R) / DX);
    if (ci0 < 0) ci0 = 0;
    if (cj0 < 0) cj0 = 0;
    if (ci1 > NX) ci1 = NX;
    if (cj1 > NY) cj1 = NY;
    if (ci0 >= ci1 || cj0 >= cj1) return 0;
    for (int l = cj0; l <= cj1; l++) {
        double dy = l * DX - yc, dy2 = dy * dy;
        for (int k = ci0; k <= ci1; k++) {
            double dx = k * DX - xc, r2 = dx * dx + dy2, d = -1;
            if (r2 < R2) d = ZG[l * w + k] - (zc - sqrt(R2 - r2));
            DEPTH[l * w + k] = d;
        }
    }
    for (int j = cj0; j < cj1; j++)
        for (int i = ci0; i < ci1; i++) {
            int on = DEPTH[j * w + i] > 0 || DEPTH[j * w + i + 1] > 0 ||
                     DEPTH[(j + 1) * w + i] > 0 || DEPTH[(j + 1) * w + i + 1] > 0;
            LAB[j * NX + i] = on ? -1 : 0;
        }
    double total = 0;
    int nlab = 0;
    for (int j0 = cj0; j0 < cj1; j0++)
        for (int i0 = ci0; i0 < ci1; i0++) {
            if (LAB[j0 * NX + i0] != -1) continue;
            /* flood fill one subset (v1.3) */
            nlab++;
            int sp = 0, n = 0, edge = 0, wrap = 0;
            STACK[sp++] = j0 * NX + i0; LAB[j0 * NX + i0] = nlab;
            while (sp) {
                int id = STACK[--sp], j = id / NX, i = id - j * NX;
                col_t *c = &COLS[n];
                c->i = i; c->j = j;
                int full_corners = DEPTH[j * w + i] > 0 && DEPTH[j * w + i + 1] > 0 &&
                                   DEPTH[(j + 1) * w + i] > 0 && DEPTH[(j + 1) * w + i + 1] > 0;
                if (i == 0 || j == 0 || i == NX - 1 || j == NY - 1) edge = 1;
                if (column_geometry(c, xc, yc, zc, R)) n++;
                /* v1.3: ground above the centre is allowed only away from the
                 * rim; at a partial column the sphere would wrap over itself. */
                if (!full_corners)
                    for (int b = 0; b <= 1; b++) for (int a = 0; a <= 1; a++)
                        if (DEPTH[(j + b) * w + i + a] > 0 && ZG[(j + b) * w + i + a] > zc) wrap = 1;
                if (i > ci0 && LAB[id - 1] == -1) { LAB[id - 1] = nlab; STACK[sp++] = id - 1; }
                if (i < ci1 - 1 && LAB[id + 1] == -1) { LAB[id + 1] = nlab; STACK[sp++] = id + 1; }
                if (j > cj0 && LAB[id - NX] == -1) { LAB[id - NX] = nlab; STACK[sp++] = id - NX; }
                if (j < cj1 - 1 && LAB[id + NX] == -1) { LAB[id + NX] = nlab; STACK[sp++] = id + NX; }
            }
            double V = 0, sx = 0, sy = 0, sxf = 0, syf = 0, Vf = 0;
            for (int q = 0; q < n; q++) {
                col_t *c = &COLS[q];
                V += c->v; sx += c->v * c->cx; sy += c->v * c->cy;
                if (c->full) {
                    sxf += c->v * ZX[c->j * NX + c->i]; syf += c->v * ZY[c->j * NX + c->i];
                    Vf += c->v;
                }
            }
                        total += V;
            if (edge) { *touch = 1; if (!rn->single) continue; }
            if (wrap) { rn->st->wrapped++; if (!rn->single) continue; }
            if (!rn->single && (V < rn->vmin || V > rn->vmax || n < rn->minc || Vf <= 0)) continue;

            /* Slip direction: the volume-weighted fall line of the full columns
             * (v1.3), downhill. */
            double delta = rn->force_delta < 100 ? rn->force_delta : atan2(-syf, -sxf);
            double cd = cos(delta), sd = sin(delta);
            double D = 0, Aterm = 0, Tord = 0, area = 0, ah_all = 0, maxneg = 0, beta_arg = 0;
            enum { MAXPHI = 16 };
            double S1[MAXPHI], S2[MAXPHI]; int bad[MAXPHI];
            for (int p = 0; p < rn->nphi; p++) { S1[p] = S2[p] = 0; bad[p] = 0; }
            /* per-column values for the Bishop iteration in forward mode */
            static double *RR = NULL, *WW = NULL, *AH = NULL, *SA = NULL, *CE = NULL;
            if (!RR) {
                size_t m = (size_t)NY * NX + 1;
                RR = malloc(m * 8); WW = malloc(m * 8); AH = malloc(m * 8); SA = malloc(m * 8); CE = malloc(m * 8);
            }
            int forward = rn->c >= 0;
            for (int q = 0; q < n; q++) {
                col_t *c = &COLS[q];
                double dx = c->cx - xc, dy = c->cy - yc, r2 = dx * dx + dy * dy;
                double hz2 = R2 - r2;
                if (hz2 < 1e-12 * R2) hz2 = 1e-12 * R2;
                double hz = sqrt(hz2);
                double cose = hz / R;                 /* (2.2) on a sphere */
                double s = dx * cd + dy * sd;         /* along-slip, + downslope of the axis */
                double Rij = sqrt(s * s + hz2);       /* distance to the rotation axis */
                double sina = -s / Rij, cosa = hz / Rij;   /* (2.3), + dips with the slip */
                double W = rn->gamma * c->v, Ah = c->ah;
                D += W * Rij * sina;                  /* (2.15) */
                Aterm += Rij * Ah / cose;
                Tord += Rij * cosa * cosa / cose * W; /* (2.27) with k = 0 */
                area += Ah / cose; ah_all += Ah;
                for (int p = 0; p < rn->nphi; p++) {
                    double ma = cose + sina * rn->tanphi[p];     /* m_alpha at F = 1 */
                    if (ma <= MALPHA_MIN) { bad[p] = 1; continue; }
                    S1[p] += Rij * W / ma; S2[p] += Rij * Ah / ma;
                }
                if (forward) {
                    RR[q] = Rij; WW[q] = W; AH[q] = Ah; SA[q] = sina; CE[q] = cose;
                    if (sina < 0) {                   /* (2.35): dip against rotation */
                        double e = -acos(cose);
                        if (e < maxneg) { maxneg = e; beta_arg = -sina * rn->tanf / cose; }
                    }
                }
            }
            rn->st->surfaces++; rn->st->columns += n;
            if (D <= 0) continue;                     /* section 2.5: F set to 100 */

            int ibin = -1;
            for (int b = 0; b < rn->nbin; b++)
                if (V >= rn->edges[b] && V < rn->edges[b + 1]) { ibin = b; break; }
            double creq[MAXPHI], cord[MAXPHI];
            for (int p = 0; p < rn->nphi; p++) {
                cord[p] = (D - Tord * rn->tanphi[p]) / Aterm;
                creq[p] = bad[p] ? NAN : (D - S1[p] * rn->tanphi[p]) / S2[p];
                if (bad[p]) rn->st->req_filtered++;
            }

            if (rn->node_best && rn->nphi) {          /* how close this node came, for refining */
                double cn = creq[0] == creq[0] ? creq[0] : cord[0];
                if (cn > *rn->node_best) *rn->node_best = cn;
            }
            double Fo = 0, Fb = 0; int conv = 0;
            if (forward) {
                Fo = (rn->c * Aterm + Tord * rn->tanf) / D;
                double F = Fo + beta_arg;
                for (int it = 0; it < 25; it++) {
                    double num = 0; int neg = 0;
                    for (int q = 0; q < n; q++) {
                        double ma = CE[q] + SA[q] * rn->tanf / F;
                        if (ma <= 0) { neg = 1; break; }
                        num += RR[q] * (rn->c * AH[q] + WW[q] * rn->tanf) / ma;
                    }
                    if (neg) break;
                    double Fn = num / D;
                    if (fabs(Fn - F) < 1e-5) { F = Fn; conv = 1; break; }
                    F = Fn;
                }
                Fb = F;
                if (!conv) rn->st->bishop_failed++;
                if (Fb > 100) Fb = 100;
            }
            if (rn->single) {
                if (V > rn->single->v) {
                    single_t *o = rn->single;
                    o->v = V; o->ah = ah_all; o->area = area; o->weight = rn->gamma * V;
                    o->f_bishop = conv ? Fb : NAN; o->f_ordinary = Fo; o->ncols = n;
                    o->delta_deg = delta * 180 / M_PI;
                    o->sigma = Tord / Aterm; o->tau = D / Aterm;
                }
                continue;
            }
            if (ENV.on && ibin == 0) {
                double sig = Tord / Aterm, tau = D / Aterm, ratio = tau / sig;
                double tx = (sx / V - ENV.x0) / ENV.size, ty = (sy / V - ENV.y0) / ENV.size;
                int it = (int)floor(tx), jt = (int)floor(ty);
                int bs = env_bin(sig), bt = env_bin(tau);
                if (it >= 0 && jt >= 0 && it < ENV.nt && jt < ENV.nt) {
                    int tile = jt * ENV.nt + it;
                    if (bs >= 0 && bt >= 0) ENV.hist[bs * ENV_NB + bt] += 1;
                    if (bs >= 0 && tau > ENV.tile_tau[tile * ENV_NB + bs]) ENV.tile_tau[tile * ENV_NB + bs] = tau;
                    if (ratio > ENV.tile_ratio[tile]) ENV.tile_ratio[tile] = ratio;
                    for (int p = 0; p < rn->nphi; p++) {
                        double *cod = &ENV.tile_cord[tile * ENV.nphi + p];
                        if (cord[p] > *cod) *cod = cord[p];
                        double *top = &ENV.tile_top[(tile * ENV.nphi + p) * ENV_NTOP];
                        if (creq[p] == creq[p] && creq[p] > top[0]) {
                            top[0] = creq[p]; top[1] = xc; top[2] = yc; top[3] = zc;
                            top[4] = R; top[5] = V; top[6] = sig; top[7] = tau;
                        }
                    }
                }
                if (ENV.cell_ratio)
                    for (int q = 0; q < n; q++) {
                        size_t cell = (size_t)COLS[q].j * NX + COLS[q].i;
                        if (ratio > ENV.cell_ratio[cell]) ENV.cell_ratio[cell] = (float)ratio;
                    }
            }
            /* Extras: only for a surface in the first class, with a valid c > 0. */
            int xf_on = XF && ibin == 0 && rn->nphi && creq[0] == creq[0] && creq[0] > 0;
            double xf_hmax = 0;
            if (xf_on) {
                double tp = rn->tanphi[0], sd_ = 0, sn_ = 0;
                for (int q = 0; q < n; q++) {
                    col_t *c = &COLS[q];
                    double dx = c->cx - xc, dy = c->cy - yc, hz2 = R2 - dx * dx - dy * dy;
                    if (hz2 < 1e-12 * R2) hz2 = 1e-12 * R2;
                    double hz = sqrt(hz2), ss = dx * cd + dy * sd, Rij = sqrt(ss * ss + hz2);
                    double sina = -ss / Rij, ma = hz / R + sina * tp, di = Rij * c->ah / ma;
                    double ci = rn->gamma * c->v / c->ah * (sina * ma - tp);
                    sd_ += di; sn_ += di * ci;
                    if (c->full && c->v / c->ah > xf_hmax) xf_hmax = c->v / c->ah;
                }
                double e = fabs(sn_ / sd_ - creq[0]);
                if (e > XF_IDERR) XF_IDERR = e;
            }
            /* Keep the extreme at every cell the mass holds. */
            double cellfrac = rn->mincellfrac * DX * DX;
            int xf_win = 0;
            if (xf_on && XF_FINAL)
                for (int q = 0; q < n && !xf_win; q++)
                    if (COLS[q].ah >= cellfrac &&
                        (float)creq[0] >= XF_FINAL[(size_t)COLS[q].j * NX + COLS[q].i]) xf_win = 1;
            for (int q = 0; q < n; q++) {
                col_t *c = &COLS[q];
                if (c->ah < cellfrac) continue;
                size_t cell = (size_t)c->j * NX + c->i;
                if (forward && conv && rn->fmin && Fb < rn->fmin[cell]) {
                    rn->fmin[cell] = (float)Fb; rn->fvol[cell] = (float)V;
                    rn->fmin_ord[cell] = (float)Fo;
                }
                if (ibin < 0 || !rn->creq) continue;
                if (xf_on && XF_FINAL) {
                    size_t nc = (size_t)NY * NX;
                    float *x = XF + cell;
                    if (xf_win && c->v / c->ah > x[XF_H_WIN * nc]) x[XF_H_WIN * nc] = (float)(c->v / c->ah);
                    if (xf_win && xf_hmax > x[XF_HMAX_WIN * nc]) x[XF_HMAX_WIN * nc] = (float)xf_hmax;
                } else if (xf_on) {
                    size_t nc = (size_t)NY * NX;
                    float *x = XF + cell;
                    double dx = c->cx - xc, dy = c->cy - yc, hz2 = R2 - dx * dx - dy * dy;
                    if (hz2 < 1e-12 * R2) hz2 = 1e-12 * R2;
                    double hz = sqrt(hz2), ss = dx * cd + dy * sd, Rij = sqrt(ss * ss + hz2);
                    double sina = -ss / Rij, ma = hz / R + sina * rn->tanphi[0], h = c->v / c->ah;
                    double cc = creq[0], fall = -(ZX[cell] * cd + ZY[cell] * sd);
                    double sg = fall / sqrt(1 + fall * fall);
                    double ci = rn->gamma * h * (sina * ma - rn->tanphi[0]);
                    if (sina > 0 && cc > x[XF_GATE * nc]) x[XF_GATE * nc] = (float)cc;
                    if (cc * sina > x[XF_CSIN * nc]) {
                        x[XF_CSIN * nc] = (float)(cc * sina);
                        x[XF_H_C * nc] = (float)h; x[XF_HMAX_C * nc] = (float)xf_hmax;
                    }
                    if (cc * sg > x[XF_CGROUND * nc]) x[XF_CGROUND * nc] = (float)(cc * sg);
                    if (ci > x[XF_LOCAL * nc]) x[XF_LOCAL * nc] = (float)ci;
                    if (h > x[XF_H_ANY * nc]) x[XF_H_ANY * nc] = (float)h;
                    if (xf_hmax > x[XF_HMAX_ANY * nc]) x[XF_HMAX_ANY * nc] = (float)xf_hmax;
                    if (cc > rn->creq[(size_t)ibin * NY * NX + cell]) {
                        x[XF_H_A * nc] = (float)h; x[XF_HMAX_A * nc] = (float)xf_hmax;
                        x[XF_SINA_A * nc] = (float)sina;
                    }
                }
                for (int p = 0; p < rn->nphi; p++) {
                    size_t off = (((size_t)p * rn->nbin + ibin) * NY * NX) + cell;
                    if (creq[p] == creq[p] && creq[p] > rn->creq[off]) {
                        rn->creq[off] = (float)creq[p];
                        rn->creq_vol[off] = (float)V;
                    }
                    if (rn->creq_ord && cord[p] > rn->creq_ord[off]) rn->creq_ord[off] = (float)cord[p];
                }
            }
        }
    return total;
}

void scoops_single(double xc, double yc, double zc, double R, double c, double phi_deg,
                   double gamma, double delta_deg, single_t *out)
{
    stats_t st; memset(&st, 0, sizeof st);
    memset(out, 0, sizeof *out);
    run_t rn; memset(&rn, 0, sizeof rn);
    rn.gamma = gamma; rn.c = c; rn.tanf = tan(phi_deg * M_PI / 180);
    rn.st = &st; rn.single = out;
    rn.force_delta = delta_deg > 360 ? 1e9 : delta_deg * M_PI / 180;
    int touch;
    sphere(&rn, xc, yc, zc, R, &touch);
}

/* Box search over the given horizontal nodes (x, y arrays).
 * Starting radius near vmin, growing by dr until vmax or the DEM edge (3.2). */
void scoops_search(const double *xn, const double *yn, long nnodes,
                   double zsmin, double zsmax, double dz, double dr,
                   double vmin, double vmax, double tol, int minc, double gamma,
                   int nphi, const double *phi_deg, int nbin, const double *edges,
                   double c, double phi_f,
                   float *creq, float *creq_vol, float *creq_ord,
                   float *fmin, float *fvol, float *fmin_ord,
                   double mincellfrac, double zspan, float *node_best, stats_t *st)
{
    double tanphi[16];
    for (int p = 0; p < nphi; p++) tanphi[p] = tan(phi_deg[p] * M_PI / 180);
    run_t rn; memset(&rn, 0, sizeof rn);
    rn.vmin = vmin; rn.vmax = vmax; rn.gamma = gamma; rn.minc = minc;
    rn.nphi = nphi; rn.nbin = nbin; rn.tanphi = tanphi; rn.edges = edges;
    rn.creq = creq; rn.creq_vol = creq_vol; rn.creq_ord = creq_ord;
    rn.c = c; rn.tanf = tan(phi_f * M_PI / 180);
    rn.fmin = fmin; rn.fvol = fvol; rn.fmin_ord = fmin_ord;
    rn.mincellfrac = mincellfrac; rn.st = st; rn.force_delta = 1e9;
    double domain = hypot(NX * DX, NY * DX);
    for (long k = 0; k < nnodes; k++) {
        double xc = xn[k], yc = yn[k];
        int ni = (int)floor(xc / DX), nj = (int)floor(yc / DX);
        if (ni < 0) ni = 0;
        if (nj < 0) nj = 0;
        if (ni > NX - 1) ni = NX - 1;
        if (nj > NY - 1) nj = NY - 1;
        double zg = DEM[nj * NX + ni], best = -1e30;
        rn.node_best = node_best ? &best : NULL;
        /* "the elevation of the first search-lattice node [is] zsmin plus a
         * multiple of zsrchres, so that the lattice node is always above the DEM" */
        double z0 = zsmin;
        if (z0 <= zg) z0 = zsmin + (floor((zg - zsmin) / dz) + 1) * dz;
        double ztop = zspan > 0 && z0 + zspan < zsmax ? z0 + zspan : zsmax;
        for (double zc = z0; zc <= ztop + 1e-9; zc += dz) {
            double lo = 0, hi = zc - zg > DX ? zc - zg : DX;
            while (quick_volume(xc, yc, zc, hi) < vmin && hi < 2 * domain) hi *= 1.25;
            if (hi >= 2 * domain) continue;
            for (int it = 0; it < 40 && hi - lo > 1e-3 * dr; it++) {
                double mid = .5 * (lo + hi), v = quick_volume(xc, yc, zc, mid);
                if (v < vmin) lo = mid;
                else if (v > vmin + tol) hi = mid;
                else { hi = mid; break; }
            }
            for (double R = hi; R < 2 * domain; R += dr) {
                int touch;
                double V = sphere(&rn, xc, yc, zc, R, &touch);
                if (touch || V > vmax) break;
            }
        }
        if (node_best) node_best[k] = (float)best;
    }
}
