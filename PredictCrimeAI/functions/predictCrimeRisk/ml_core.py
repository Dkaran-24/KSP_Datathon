"""
Pure-Python ML primitives (no numpy/scikit-learn) so the Catalyst function
bundle stays small and deploy-safe.
"""
import math
import statistics
import random


# ---------- Small linear algebra (Gaussian elimination w/ partial pivoting) ----------

def solve_linear_system(A, b):
    """Solve Ax = b for square A using Gaussian elimination with partial pivoting.
    A: list of lists (n x n), b: list (n). Returns list x (n)."""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]

    for col in range(n):
        # partial pivot
        pivot_row = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[pivot_row][col]) < 1e-12:
            raise ValueError("Singular matrix - insufficient/degenerate data for regression")
        M[col], M[pivot_row] = M[pivot_row], M[col]

        pivot = M[col][col]
        for j in range(col, n + 1):
            M[col][j] /= pivot

        for r in range(n):
            if r != col:
                factor = M[r][col]
                for j in range(col, n + 1):
                    M[r][j] -= factor * M[col][j]

    return [M[i][n] for i in range(n)]


def matmul_At_A(X):
    """X: list of rows (each a list of features). Returns X^T X as n_features x n_features."""
    n_feat = len(X[0])
    result = [[0.0] * n_feat for _ in range(n_feat)]
    for row in X:
        for i in range(n_feat):
            for j in range(n_feat):
                result[i][j] += row[i] * row[j]
    return result


def matmul_At_y(X, y):
    n_feat = len(X[0])
    result = [0.0] * n_feat
    for row, yi in zip(X, y):
        for i in range(n_feat):
            result[i] += row[i] * yi
    return result


def ols_fit(X, y):
    """Ordinary least squares via normal equations. X: rows of features (incl intercept term),
    y: targets. Returns (beta, r_squared, residual_std)."""
    XtX = matmul_At_A(X)
    Xty = matmul_At_y(X, y)
    beta = solve_linear_system(XtX, Xty)

    y_mean = sum(y) / len(y)
    y_hat = [sum(b * x for b, x in zip(beta, row)) for row in X]
    ss_res = sum((yi - yhi) ** 2 for yi, yhi in zip(y, y_hat))
    ss_tot = sum((yi - y_mean) ** 2 for yi in y) or 1e-9
    r_squared = max(0.0, 1 - ss_res / ss_tot)
    residual_std = math.sqrt(ss_res / max(1, len(y) - len(beta)))
    return beta, r_squared, residual_std


def fit_seasonal_trend_model(counts):
    """Fit y ~ intercept + trend*t + seasonal(sin/cos, period 12) using OLS.
    counts: list of monthly counts in chronological order.
    Returns dict with beta, r_squared, residual_std, and a predict(t) closure."""
    n = len(counts)
    X = []
    for t in range(n):
        angle = 2 * math.pi * t / 12.0
        X.append([1.0, float(t), math.sin(angle), math.cos(angle)])
    beta, r_squared, residual_std = ols_fit(X, counts)

    def predict(t):
        angle = 2 * math.pi * t / 12.0
        row = [1.0, float(t), math.sin(angle), math.cos(angle)]
        return sum(b * x for b, x in zip(beta, row))

    return {
        "beta": beta,          # [intercept, trend_slope, seasonal_sin_coef, seasonal_cos_coef]
        "r_squared": r_squared,
        "residual_std": residual_std,
        "predict": predict
    }


# ---------- Adaptive z-score anomaly detection ----------

def zscore_anomaly(historical_counts, latest_count):
    """Per-entity adaptive anomaly score: how many standard deviations is the
    latest value from this entity's OWN historical mean (not a global fixed ratio)."""
    if len(historical_counts) < 2:
        mean = historical_counts[0] if historical_counts else 0.0
        std = 0.0
    else:
        mean = statistics.mean(historical_counts)
        std = statistics.stdev(historical_counts)

    if std < 1e-9:
        # No historical variance to compare against.
        z = 0.0 if latest_count <= mean else (3.5 if latest_count > 0 else 0.0)
    else:
        z = (latest_count - mean) / std

    return {"mean": mean, "std": std, "z_score": round(z, 2)}


# ---------- K-means clustering (pure python) ----------

def euclidean(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def kmeans(vectors, k, iterations=100, seed=42):
    """vectors: list of feature vectors (same length). Returns (labels, centroids)."""
    rnd = random.Random(seed)
    n = len(vectors)
    k = min(k, n)
    if k <= 0:
        return [0] * n, []

    # k-means++ style init: pick first randomly, rest weighted by distance^2
    centroids = [vectors[rnd.randrange(n)]]
    while len(centroids) < k:
        dists = [min(euclidean(v, c) ** 2 for c in centroids) for v in vectors]
        total = sum(dists) or 1e-9
        r = rnd.random() * total
        acc = 0.0
        for i, d in enumerate(dists):
            acc += d
            if acc >= r:
                centroids.append(vectors[i])
                break
        else:
            centroids.append(vectors[rnd.randrange(n)])

    labels = [0] * n
    for _ in range(iterations):
        new_labels = []
        for v in vectors:
            dists = [euclidean(v, c) for c in centroids]
            new_labels.append(dists.index(min(dists)))

        if new_labels == labels:
            break
        labels = new_labels

        dim = len(vectors[0])
        sums = [[0.0] * dim for _ in range(k)]
        counts = [0] * k
        for v, lab in zip(vectors, labels):
            counts[lab] += 1
            for d in range(dim):
                sums[lab][d] += v[d]

        new_centroids = []
        for c_idx in range(k):
            if counts[c_idx] == 0:
                new_centroids.append(centroids[c_idx])
            else:
                new_centroids.append([s / counts[c_idx] for s in sums[c_idx]])
        centroids = new_centroids

    return labels, centroids


def pearson_correlation(x, y):
    n = len(x)
    if n < 3:
        return 0.0
    mean_x, mean_y = sum(x) / n, sum(y) / n
    cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
    std_x = math.sqrt(sum((xi - mean_x) ** 2 for xi in x))
    std_y = math.sqrt(sum((yi - mean_y) ** 2 for yi in y))
    if std_x < 1e-9 or std_y < 1e-9:
        return 0.0
    return cov / (std_x * std_y)