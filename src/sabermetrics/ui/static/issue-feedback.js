/* One explicitly submitted report. No background capture or third-party code. */
(function () {
    'use strict';
    const launcher = document.getElementById('feedback-launcher');
    const panel = document.getElementById('feedback-panel');
    if (!launcher || !panel || !panel.showModal || !window.crypto.randomUUID) return;
    const form = document.getElementById('feedback-form');
    const fields = document.getElementById('feedback-fields');
    const description = document.getElementById('feedback-description');
    const details = document.getElementById('feedback-details');
    const includeContext = document.getElementById('feedback-context');
    const input = document.getElementById('feedback-image');
    const capture = document.getElementById('feedback-capture');
    const zone = document.getElementById('feedback-dropzone');
    const preview = document.getElementById('feedback-preview');
    const previewImage = document.getElementById('feedback-preview-image');
    const status = document.getElementById('feedback-status');
    const submit = document.getElementById('feedback-submit');
    const success = document.getElementById('feedback-success');
    const signin = document.getElementById('feedback-signin');
    let csrfToken = document.querySelector('meta[name="csrf-token"]').content;
    let refreshSession = false;
    let attachment = null;
    let previewURL = null;
    let requestID = crypto.randomUUID();
    let frozen = null;
    let busy = false;
    let pendingImage = 0;
    let imageLoading = false;

    function dirty() { return description.value.trim() || details.value.trim() || attachment || frozen; }
    function close() { panel.close(); }
    function reset() {
        form.reset(); removeImage(); frozen = null; fields.disabled = false;
        zone.hidden = false;
        requestID = crypto.randomUUID(); status.textContent = '';
        submit.textContent = 'Send feedback';
        signin.hidden = true; refreshSession = false;
    }
    function removeImage() {
        pendingImage += 1;
        imageLoading = false; submit.disabled = busy;
        attachment = null; input.value = ''; preview.hidden = true;
        previewImage.removeAttribute('src');
        if (previewURL) URL.revokeObjectURL(previewURL);
        previewURL = null;
    }
    async function chooseImage(file) {
        if (busy || frozen || !file) return;
        if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 10000000 || file.size === 0) {
            status.textContent = 'Choose a PNG, JPEG, or WebP image up to 10 MB.';
            input.value = ''; return;
        }
        const selection = ++pendingImage;
        imageLoading = true; submit.disabled = true;
        const url = URL.createObjectURL(file);
        const image = new Image();
        image.src = url;
        try {
            await image.decode();
            if (image.naturalWidth * image.naturalHeight > 25000000) throw new Error('dimensions');
            if (selection !== pendingImage || busy || frozen) { URL.revokeObjectURL(url); return; }
            if (previewURL) URL.revokeObjectURL(previewURL);
            previewURL = url; attachment = file;
            previewImage.src = url;
            document.getElementById('feedback-image-name').textContent = file.name;
            preview.hidden = false; status.textContent = '';
        } catch (_) {
            URL.revokeObjectURL(url); input.value = '';
            status.textContent = 'Choose a valid image up to 25 megapixels.';
        } finally {
            if (selection === pendingImage) { imageLoading = false; submit.disabled = busy; }
        }
    }
    launcher.hidden = false;
    launcher.addEventListener('click', function () {
        if (!success.hidden) { success.hidden = true; form.hidden = false; }
        panel.showModal(); launcher.setAttribute('aria-expanded', 'true');
        description.focus();
    });
    document.getElementById('feedback-close').addEventListener('click', close);
    document.getElementById('feedback-done').addEventListener('click', close);
    panel.addEventListener('close', function () {
        launcher.setAttribute('aria-expanded', 'false'); launcher.focus();
    });
    input.addEventListener('change', function () { chooseImage(input.files[0]); });
    if (capture && navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia) {
        capture.hidden = false;
        capture.addEventListener('click', async function () {
            if (!includeContext.checked || busy || frozen) return;
            let stream;
            try {
                stream = await navigator.mediaDevices.getDisplayMedia({video: true, audio: false});
                const video = document.createElement('video');
                video.srcObject = stream; video.muted = true;
                await video.play();
                const canvas = document.createElement('canvas');
                canvas.width = video.videoWidth; canvas.height = video.videoHeight;
                canvas.getContext('2d').drawImage(video, 0, 0);
                const blob = await new Promise(function (resolve) { canvas.toBlob(resolve, 'image/png'); });
                if (!blob) throw new Error('capture');
                await chooseImage(new File([blob], 'deck-lab-screenshot.png', {type: 'image/png'}));
            } catch (error) {
                if (error.name !== 'NotAllowedError') status.textContent = 'Screen capture is unavailable. Choose or paste an image instead.';
            } finally {
                if (stream) stream.getTracks().forEach(function (track) { track.stop(); });
            }
        });
    }
    includeContext.addEventListener('change', function () {
        zone.hidden = !includeContext.checked;
        if (!includeContext.checked) removeImage();
    });
    document.getElementById('feedback-remove').addEventListener('click', function () { if (!frozen && !busy) removeImage(); });
    zone.addEventListener('dragover', function (e) { e.preventDefault(); if (!frozen && !busy) zone.classList.add('is-dragging'); });
    zone.addEventListener('dragleave', function () { zone.classList.remove('is-dragging'); });
    zone.addEventListener('drop', function (e) {
        e.preventDefault(); zone.classList.remove('is-dragging');
        if (e.dataTransfer.files.length !== 1) { status.textContent = 'Attach one image at a time.'; return; }
        chooseImage(e.dataTransfer.files[0]);
    });
    panel.addEventListener('paste', function (e) {
        const files = Array.from(e.clipboardData.files);
        if (files.length) { e.preventDefault(); if (files.length === 1) chooseImage(files[0]); else status.textContent = 'Attach one image at a time.'; }
    });
    window.addEventListener('beforeunload', function (e) {
        if (dirty()) { e.preventDefault(); e.returnValue = ''; }
    });
    form.addEventListener('submit', async function (e) {
        e.preventDefault();
        if (busy || imageLoading || (!frozen && !form.reportValidity())) return;
        if (!frozen) {
            frozen = new FormData(form);
            frozen.append('submission_id', requestID);
            frozen.append('page_path', window.location.pathname);
            frozen.append('include_context', includeContext.checked ? '1' : '0');
            frozen.append('viewport_width', String(window.innerWidth));
            frozen.append('viewport_height', String(window.innerHeight));
            if (attachment) frozen.append('screenshot', attachment, attachment.name);
        }
        busy = true; fields.disabled = true; submit.disabled = true;
        status.textContent = 'Sending your feedback…';
        submit.textContent = 'Sending…';
        const controller = new AbortController();
        const timeout = setTimeout(function () { controller.abort(); }, 65000);
        try {
            if (refreshSession) {
                const sessionResponse = await fetch('/feedback/session', {credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal, headers: {'Accept': 'application/json'}});
                const sessionBody = await sessionResponse.json();
                if (!sessionResponse.ok || typeof sessionBody.csrf_token !== 'string') {
                    status.textContent = 'Sign in in the new tab, then retry here. Your report is still saved in this form.';
                    submit.textContent = 'Retry sending'; return;
                }
                csrfToken = sessionBody.csrf_token; refreshSession = false; signin.hidden = true;
            }
            const response = await fetch(form.action, {
                method: 'POST', body: frozen, credentials: 'same-origin',
                redirect: 'error', signal: controller.signal,
                headers: { 'X-CSRFToken': csrfToken, 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }
            });
            const body = await response.json();
            if (response.ok && body.ok === true) {
                reset(); form.hidden = true; success.hidden = false; success.focus();
            } else {
                status.textContent = body.error || 'We could not confirm delivery. Please retry.';
                if (response.status === 401 || body.code === 'session_expired') {
                    refreshSession = true; signin.hidden = false;
                }
                // Definitive validation failures are safe to edit. Uncertain
                // deliveries retry the identical payload and submission ID.
                if (!refreshSession && (response.status === 400 || response.status === 413)) {
                    frozen = null; fields.disabled = false;
                }
                submit.textContent = 'Retry sending';
            }
        } catch (_) {
            status.textContent = 'We couldn’t confirm delivery. Your report is still here; retry to check and send it safely.';
            submit.textContent = 'Retry sending';
        } finally {
            clearTimeout(timeout); busy = false; submit.disabled = false;
        }
    });
}());
