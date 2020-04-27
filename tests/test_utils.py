import numpy as np
from gnn.utils import np_split_by_size


def test_np_split_by_size():
    ref = [[0], [1, 2], [3, 4, 5]]
    res = np_split_by_size([0, 1, 2, 3, 4, 5], [1, 2, 3])

    for i, j in zip(ref, res):
        assert np.array_equal(i, j)