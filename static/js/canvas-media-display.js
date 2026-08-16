/**
 * 经典画布媒体显示：预览档常驻、图片 DOM 复用、同时只活一路 video。
 * 纯几何与签名函数可在 Node 里单测；DOM 操作由画布页注入 helpers。
 */
(function (root, factory) {
    const api = factory();
    root.CanvasMediaDisplay = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
    const VIEW_MARGIN_PX = 220;
    const FALLBACK_NODE_HEIGHT = 480;
    const OUTPUT_GRID_EAGER_COUNT = 8;

    function shouldSwapCanvasImageToOriginal() {
        return false;
    }

    function worldViewRect(viewport, boardW, boardH) {
        const scale = Number(viewport && viewport.scale) || 1;
        return {
            x: -(Number(viewport && viewport.x) || 0) / scale,
            y: -(Number(viewport && viewport.y) || 0) / scale,
            w: Math.max(0, Number(boardW) || 0) / scale,
            h: Math.max(0, Number(boardH) || 0) / scale
        };
    }

    function nodeWorldRect(node, fallbackSize) {
        const fallback = fallbackSize || {};
        const w = Number(node && node.w) > 0 ? Number(node.w) : (Number(fallback.w) || 260);
        const storedH = Number(node && node.h);
        const fallbackH = Number(fallback.h);
        const h = storedH > 0 ? storedH : Math.max(fallbackH || 0, FALLBACK_NODE_HEIGHT);
        return {
            x: Number(node && node.x) || 0,
            y: Number(node && node.y) || 0,
            w,
            h
        };
    }

    function rectsOverlap(a, b, margin) {
        const pad = Number(margin) || 0;
        if (!a || !b) return false;
        return a.x + a.w >= b.x - pad
            && a.x <= b.x + b.w + pad
            && a.y + a.h >= b.y - pad
            && a.y <= b.y + b.h + pad;
    }

    function nodeNearWorldView(node, viewport, boardW, boardH, marginPx, fallbackSize) {
        const scale = Number(viewport && viewport.scale) || 1;
        const margin = (Number(marginPx) || VIEW_MARGIN_PX) / scale;
        return rectsOverlap(
            nodeWorldRect(node, fallbackSize),
            worldViewRect(viewport, boardW, boardH),
            margin
        );
    }

    function mediaOriginalUrl(el) {
        if (!el) return '';
        const dataset = el.dataset || {};
        return String(dataset.originalSrc || dataset.url || '').trim();
    }

    function mediaSignature(el) {
        if (!el) return '';
        const tag = String(el.tagName || '').toLowerCase();
        const url = mediaOriginalUrl(el);
        return url ? `${tag}:${url}` : '';
    }

    function collectMediaSlots(root) {
        if (!root || !root.querySelectorAll) return [];
        return [...root.querySelectorAll('img[data-preview-src], img[data-original-src], video[data-url], audio[data-url]')];
    }

    function outputEagerMediaCount(total, options) {
        const n = Math.max(0, Number(total) || 0);
        const opts = options || {};
        if (opts.gridSplit || opts.expanded) return n;
        return Math.min(OUTPUT_GRID_EAGER_COUNT, n);
    }

    function shouldRestorePreviewSrc(src, preview) {
        return Boolean(preview && src && src !== preview);
    }

    function restorePreviewSources(root) {
        if (!root || !root.querySelectorAll) return 0;
        let restored = 0;
        root.querySelectorAll('img[data-preview-src][data-original-src]').forEach(img => {
            const preview = img.dataset.previewSrc || '';
            const src = img.getAttribute('src') || '';
            delete img.dataset.selectedHighResTarget;
            if (!shouldRestorePreviewSrc(src, preview)) return;
            img.src = preview;
            restored += 1;
        });
        return restored;
    }

    function mountPreviewImage(img) {
        if (!img) return false;
        const preview = img.dataset && img.dataset.previewSrc || '';
        if (!preview) return false;
        img.classList.remove('canvas-media-deferred');
        if (img.dataset) delete img.dataset.mediaUnmounted;
        if (img.getAttribute('src') !== preview) {
            img.setAttribute('src', preview);
            return true;
        }
        return false;
    }

    function unmountPreviewImage(img) {
        if (!img) return false;
        img.classList.add('canvas-media-deferred');
        if (img.dataset) img.dataset.mediaUnmounted = '1';
        if (!img.getAttribute('src')) return false;
        img.removeAttribute('src');
        return true;
    }

    function syncMountedPreviews(root, nodes, viewport, boardW, boardH, fallbackSizeFor) {
        if (!root || !root.querySelectorAll) return {mounted: 0, unmounted: 0};
        const nodeById = new Map((nodes || []).filter(node => node && node.id).map(node => [node.id, node]));
        let mounted = 0;
        let unmounted = 0;
        root.querySelectorAll('.node').forEach(nodeEl => {
            const node = nodeById.get(nodeEl.dataset && nodeEl.dataset.id);
            if (!node) return;
            const fallback = typeof fallbackSizeFor === 'function' ? fallbackSizeFor(node) : fallbackSizeFor;
            const near = nodeNearWorldView(node, viewport, boardW, boardH, VIEW_MARGIN_PX, fallback);
            if (node.type === 'output') {
                nodeEl.querySelectorAll('.output-img-wrap').forEach(wrap => {
                    if (wrap.querySelector('video[data-url], audio[data-url]')) return;
                    const img = wrap.querySelector('img[data-preview-src]');
                    if (!img) return;
                    if (near) {
                        if (mountPreviewImage(img)) mounted += 1;
                    } else if (unmountPreviewImage(img)) {
                        unmounted += 1;
                    }
                });
                return;
            }
            nodeEl.querySelectorAll('img[data-preview-src]').forEach(img => {
                if (img.closest && img.closest('.output-img-wrap')) return;
                if (near) {
                    if (mountPreviewImage(img)) mounted += 1;
                } else if (unmountPreviewImage(img)) {
                    unmounted += 1;
                }
            });
        });
        return {mounted, unmounted};
    }

    function findMatchingSlot(oldMedia, news, used) {
        const url = mediaOriginalUrl(oldMedia);
        if (!url) return -1;
        const oldTag = String(oldMedia.tagName || '').toLowerCase();
        const sameTag = news.findIndex((el, index) => !used.has(index) && mediaOriginalUrl(el) === url && String(el.tagName || '').toLowerCase() === oldTag);
        if (sameTag >= 0) return sameTag;
        if (oldTag === 'video') {
            return news.findIndex((el, index) => !used.has(index) && mediaOriginalUrl(el) === url && String(el.tagName || '').toLowerCase() === 'img');
        }
        return -1;
    }

    function transplantReusableMedia(oldRoot, newRoot) {
        const olds = collectMediaSlots(oldRoot);
        const news = collectMediaSlots(newRoot);
        const used = new Set();
        olds.forEach(oldMedia => {
            const index = findMatchingSlot(oldMedia, news, used);
            if (index < 0) return;
            const match = news[index];
            used.add(index);
            const oldTag = String(oldMedia.tagName || '').toLowerCase();
            if (oldTag === 'img') {
                oldMedia.className = match.className;
                oldMedia.draggable = match.draggable;
                if (match.getAttribute('alt') != null) oldMedia.alt = match.getAttribute('alt') || '';
                match.replaceWith(oldMedia);
                return;
            }
            match.replaceWith(oldMedia);
            if (oldTag === 'video') {
                oldMedia.parentElement?.querySelector?.('.canvas-video-play')?.style?.setProperty('display', 'none');
            }
        });
    }

    function releaseVideoElement(video) {
        if (!video || String(video.tagName || '').toLowerCase() !== 'video') return;
        try { video.pause(); } catch (e) {}
        try { video.removeAttribute('src'); } catch (e) {}
        try { video.load?.(); } catch (e) {}
    }

    function deactivateVideo(video, helpers) {
        if (!video || String(video.tagName || '').toLowerCase() !== 'video') return false;
        const original = mediaOriginalUrl(video) || String(video.getAttribute('src') || '');
        if (!original || !helpers || typeof helpers.previewHtml !== 'function') {
            releaseVideoElement(video);
            return false;
        }
        const wrap = video.closest?.('.media-card, .image-preview-wrap, .output-img-wrap') || video.parentElement;
        const tpl = document.createElement('template');
        tpl.innerHTML = helpers.previewHtml(original);
        const img = tpl.content.firstElementChild;
        releaseVideoElement(video);
        if (!img) return false;
        video.replaceWith(img);
        const playBtn = wrap?.querySelector?.('.canvas-video-play');
        if (playBtn) playBtn.style.display = '';
        if (typeof helpers.onRestored === 'function') helpers.onRestored(img, wrap);
        return true;
    }

    function pickKeptVideo(root, preferred) {
        if (preferred && preferred.isConnected) return preferred;
        const videos = root && root.querySelectorAll ? [...root.querySelectorAll('video[data-url]')] : [];
        return videos.find(video => !video.paused && !video.ended) || videos[0] || null;
    }

    function enforceSingleLiveVideo(root, preferred, helpers) {
        const kept = pickKeptVideo(root, preferred);
        if (!root || !root.querySelectorAll) return kept;
        root.querySelectorAll('video[data-url]').forEach(video => {
            if (video !== kept) deactivateVideo(video, helpers);
        });
        return kept;
    }

    function unloadOffscreenVideos(root, nodes, viewport, boardW, boardH, fallbackSizeFor, helpers) {
        if (!root || !root.querySelectorAll) return 0;
        let unloaded = 0;
        root.querySelectorAll('video[data-url]').forEach(video => {
            const nodeEl = video.closest?.('.node');
            const node = (nodes || []).find(item => item && item.id === nodeEl?.dataset?.id);
            const fallback = typeof fallbackSizeFor === 'function' ? fallbackSizeFor(node) : fallbackSizeFor;
            if (!node || !nodeNearWorldView(node, viewport, boardW, boardH, VIEW_MARGIN_PX, fallback)) {
                if (deactivateVideo(video, helpers)) unloaded += 1;
            }
        });
        return unloaded;
    }

    function nodeHasReusableMedia(node) {
        if (!node) return false;
        if (node.type === 'image' && node.url) return true;
        return node.type === 'output';
    }

    return {
        VIEW_MARGIN_PX,
        FALLBACK_NODE_HEIGHT,
        OUTPUT_GRID_EAGER_COUNT,
        shouldSwapCanvasImageToOriginal,
        worldViewRect,
        nodeWorldRect,
        rectsOverlap,
        nodeNearWorldView,
        mediaOriginalUrl,
        mediaSignature,
        collectMediaSlots,
        outputEagerMediaCount,
        shouldRestorePreviewSrc,
        restorePreviewSources,
        mountPreviewImage,
        unmountPreviewImage,
        syncMountedPreviews,
        transplantReusableMedia,
        deactivateVideo,
        enforceSingleLiveVideo,
        unloadOffscreenVideos,
        nodeHasReusableMedia
    };
});
