# -*- coding: utf-8 -*-
"""قفل مشترك واحد بين السكانر (main.py) ومستمع الأوامر (commander.py).

الهدف: منع سباق last-write-wins على الـGist — أي عملية تقرأ الحالة
وتعدّلها ثم تحفظها يجب أن تتم داخل `with state_locked():`.

وُضع في وحدة مستقلة لتفادي الاستيراد الدائري (commander يستورد main)."""

import fcntl
import os
import time

LOCK_TIMEOUT = 180  # أقصى انتظار للقفل قبل الاستسلام (ثواني)


def lock_file():
    # ~/bot/state.lock على الـVM (مجلد الأب لمجلد الكود)
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "state.lock")


class state_locked:
    """قفل حصري (fcntl) على state.lock مع مهلة."""

    def __enter__(self):
        self.f = open(lock_file(), "w")
        start = time.time()
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except BlockingIOError:
                if time.time() - start > LOCK_TIMEOUT:
                    self.f.close()
                    raise TimeoutError(
                        "state.lock busy > %ds" % LOCK_TIMEOUT)
                time.sleep(0.5)

    def __exit__(self, *a):
        try:
            fcntl.flock(self.f, fcntl.LOCK_UN)
        finally:
            self.f.close()
        return False
