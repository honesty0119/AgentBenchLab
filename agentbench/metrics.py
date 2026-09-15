"""Small transparent metrics for interview exercises and optional diagnostics.

ROUGE-L operates on caller-supplied tokens. For Chinese, choose a segmentation policy
explicitly; do not compare scores produced with different tokenization.
These lexical metrics are not the hard task-success criterion.
"""


def lcs_length(a, b):
    if len(a) < len(b):
        a, b = b, a
    previous = [0] * (len(b) + 1)
    for x in a:
        current = [0]
        for j, y in enumerate(b, start=1):
            current.append(previous[j - 1] + 1 if x == y else max(previous[j], current[-1]))
        previous = current
    return previous[-1]


def rouge_l(prediction, reference):
    overlap = lcs_length(prediction, reference)
    precision = overlap / len(prediction) if prediction else 0.0
    recall = overlap / len(reference) if reference else 0.0
    return {"precision": precision, "recall": recall,
            "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0}


def recall_at_k(retrieved, relevant, k):
    if k < 1:
        raise ValueError("k must be positive")
    if not relevant:
        return None
    return len(set(retrieved[:k]) & set(relevant)) / len(set(relevant))
