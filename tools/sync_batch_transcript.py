"""Cached lexical correspondence for transcript-backed lyric alignment."""
import numpy as np
from numba import njit


@njit(cache=True)
def lexical_positions(expected, heard):
    n, m = len(expected), len(heard)
    cost = np.zeros((n + 1, m + 1), dtype=np.int32)
    cost[:, 0] = np.arange(n + 1)
    back = np.zeros((n, m), dtype=np.uint8)
    for i in range(n):
        for j in range(m):
            diagonal = cost[i, j] + (expected[i] != heard[j])
            deletion, insertion = cost[i, j + 1] + 1, cost[i + 1, j] + 1
            direction = 0 if diagonal <= deletion and diagonal <= insertion else 1 if deletion <= insertion else 2
            back[i, j] = direction
            cost[i + 1, j + 1] = diagonal if direction == 0 else deletion if direction == 1 else insertion
    j = int(np.argmin(cost[n])) - 1
    i = n - 1
    positions = np.full(n, -1, dtype=np.int32)
    while i >= 0 and j >= 0:
        direction = back[i, j]
        if direction == 0:
            if expected[i] == heard[j]:
                positions[i] = j
            i -= 1
            j -= 1
        elif direction == 1:
            i -= 1
        else:
            j -= 1
    return positions

