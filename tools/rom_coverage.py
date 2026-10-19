"""Half-open interval operations shared by reconstruction and completion gates."""

def merge(intervals):
    result = []
    for start, end in sorted(intervals):
        if start < 0 or end < start:
            raise ValueError(f'invalid interval [{start}, {end})')
        if start == end:
            continue
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def gaps(intervals, windows):
    merged = merge(intervals)
    result = []
    for low, high in merge(windows):
        pos = low
        for start, end in merged:
            if end <= pos or start >= high:
                continue
            if start > pos:
                result.append((pos, start))
            pos = max(pos, min(high, end))
        if pos < high:
            result.append((pos, high))
    return result


def size(intervals):
    return sum(end - start for start, end in merge(intervals))
