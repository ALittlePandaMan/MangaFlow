from app.tasks.admission import PageAdmission


def claim(queue,token):
    assert queue.claim(token,lambda: True)


def test_large_batch_rotates_between_projects_and_preserves_each_project_order():
    queue = PageAdmission()
    for index in range(337):
        queue.add(f"book-{index}","book",interactive=False)
    queue.add("other-0","other",interactive=False)
    queue.add("other-1","other",interactive=False)
    for token in ("book-0","other-0","book-1","other-1","book-2"):
        claim(queue,token)


def test_interactive_priority_is_bounded_so_batches_still_progress():
    queue = PageAdmission()
    queue.add("batch","book",interactive=False)
    for index in range(6):
        queue.add(f"edit-{index}","editing",interactive=True)
    assert not queue.claim("batch",lambda: True)
    for index in range(3):
        claim(queue,f"edit-{index}")
    assert not queue.claim("edit-3",lambda: True)
    claim(queue,"batch")
    claim(queue,"edit-3")


def test_cancelled_head_releases_other_projects_without_acquiring_a_resource():
    queue = PageAdmission()
    queue.add("cancelled","book",interactive=False)
    queue.add("ready","other",interactive=False)
    assert not queue.claim("cancelled",lambda: False)
    queue.remove("cancelled")
    claim(queue,"ready")
    assert not queue.queues[False]


def test_explicit_resume_moves_an_existing_waiter_ahead_of_batch_preparations():
    queue = PageAdmission()
    for index in range(337):
        queue.add(f"batch-{index}","book",interactive=False)
    queue.promote("batch-336")
    claim(queue,"batch-336")
    claim(queue,"batch-0")
    queue.promote("not-ready")
    # Promotion must not create a phantom head which has no coroutine to run.
    assert not queue.queues[True]
