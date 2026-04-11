(function () {
    const runtimeConfig = window.OPENCLAW_RUNTIME_CONFIG || {};
    const SERVER_URL = runtimeConfig.serverUrl || window.location.origin;

    function escapeHtml(text) {
        return String(text || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function formatSignedScore(value) {
        const number = Number(value || 0);
        if (number > 0) return '+' + number;
        return String(number);
    }

    function formatMetricValue(section) {
        const value = Number(section && section.metric_value ? section.metric_value : 0);
        if (section.value_kind === 'delta') return formatSignedScore(value);
        return String(value);
    }

    function pickDailyFlavor(summaryDate, lines) {
        const pool = Array.isArray(lines) ? lines.filter(function (line) {
            return String(line || '').trim().length > 0;
        }) : [];
        if (pool.length === 0) return '';

        const seedText = String(summaryDate || new Date().toISOString().slice(0, 10));
        let hash = 0;
        for (let i = 0; i < seedText.length; i += 1) {
            hash = ((hash * 31) + seedText.charCodeAt(i)) >>> 0;
        }
        return pool[hash % pool.length];
    }


    function buildTitleInfo(score, labels) {
        if (score > 600) return { text: labels.rankDaCongMing || '大聪明', class: 'title-da-cong-ming' };
        if (score >= 300) return { text: labels.rankOpenClaw || 'OpenClaw', class: 'title-openclaw' };
        if (score >= 243) return { text: labels.rankHomarus || '全知澳龙', class: 'title-homarus' };
        if (score >= 183) return { text: labels.rankNephropidae || '神经网络鳌', class: 'title-nephropidae' };
        if (score >= 123) return { text: labels.rankPleocyemata || '草泥虾', class: 'title-pleocyemata' };
        if (score >= 63) return { text: labels.rankDecapoda || '算法小虾', class: 'title-decapoda' };
        if (score >= 0) return { text: labels.rankMalacostraca || '萌新虾兵', class: 'title-malacostraca' };
        return { text: labels.rankTail || '龙虾尾', class: 'title-tail' };
    }

    function normalizeAvatarMap(raw) {
        const empty = { players: {}, nicknames: {} };
        if (!raw || typeof raw !== 'object') return empty;

        const players = (raw.players && typeof raw.players === 'object') ? raw.players : {};
        const nicknames = (raw.nicknames && typeof raw.nicknames === 'object') ? raw.nicknames : {};

        if (Object.keys(players).length || Object.keys(nicknames).length) {
            return { players: players, nicknames: nicknames };
        }

        return { players: raw, nicknames: {} };
    }

    async function fetchAvatarMap() {
        const candidates = [
            window.location.origin + '/data/avatar_map.json',
            window.location.origin + '/avatar_map.json'
        ];

        for (const url of candidates) {
            try {
                const resp = await fetch(url, { cache: 'no-store' });
                if (!resp.ok) continue;
                const payload = await resp.json();
                return normalizeAvatarMap(payload);
            } catch (error) {
                console.warn('avatar map fetch failed:', url, error);
            }
        }

        return emptyAvatarMap();
    }

    function emptyAvatarMap() {
        return { players: {}, nicknames: {} };
    }

    function resolveAvatarKey(player, avatarMap) {
        const current = player || {};
        const playerId = String(current.player_id || '').trim();
        const nickname = String(current.nickname || '').trim();
        const map = avatarMap || emptyAvatarMap();

        const mappedById = String((map.players && map.players[playerId]) || '').trim();
        if (mappedById) return mappedById;

        const mappedByName = String((map.nicknames && map.nicknames[nickname]) || '').trim();
        if (mappedByName) return mappedByName;

        return playerId || nickname || 'unknown';
    }

    function buildAvatarSrc(avatarKey, ext) {
        return 'assets/avatar/' + encodeURIComponent(String(avatarKey || '')) + '.' + ext;
    }

    function avatarHtml(player, avatarMap, labels) {
        const avatarKey = resolveAvatarKey(player, avatarMap);
        const name = escapeHtml(player.nickname || player.player_id || labels.unknownPlayer || 'Unknown');
        const initial = escapeHtml(String(player.nickname || player.player_id || 'U').slice(0, 1).toUpperCase());

        return (
            '<div class="settlement-avatar-shell">' +
                '<img class="settlement-avatar" data-avatar-key="' + escapeHtml(avatarKey) + '" alt="' + name + '">' +
                '<div class="settlement-avatar-fallback">' + initial + '</div>' +
            '</div>'
        );
    }

    function bindAvatarLoaders(container) {
        const imgs = container.querySelectorAll('.settlement-avatar[data-avatar-key]');
        const exts = ['webp', 'png', 'jpg', 'jpeg'];

        imgs.forEach(function (img) {
            const avatarKey = img.getAttribute('data-avatar-key');
            if (!avatarKey) return;
            const shell = img.closest('.settlement-avatar-shell');

            function tryLoad(index) {
                if (index >= exts.length) {
                    img.remove();
                    if (shell) shell.classList.add('missing');
                    return;
                }
                img.onload = function () {
                    if (shell) shell.classList.add('loaded');
                };
                img.onerror = function () {
                    tryLoad(index + 1);
                };
                img.src = buildAvatarSrc(avatarKey, exts[index]);
            }

            tryLoad(0);
        });
    }

    function getPlayerValueLine(section, player, labels) {
        if (section.value_kind === 'count') {
            return labels.achievementValuePrefix + '<strong>' + escapeHtml(String(player.achievement_count || 0)) + '</strong>' + labels.achievementUnit;
        }
        if (section.value_kind === 'delta') {
            return labels.deltaValuePrefix + '<strong>' + escapeHtml(formatSignedScore(player.gambling_delta || 0)) + '</strong>' + labels.scoreUnit;
        }
        return labels.scoreValuePrefix + '<strong>' + escapeHtml(String(player.score || 0)) + '</strong>' + labels.scoreUnit;
    }

    function getPlayerNote(section, player, labels) {
        if (section.value_kind === 'count') {
            return labels.achievementNotePrefix + escapeHtml(String(player.score || 0)) + labels.scoreUnit;
        }
        if (section.value_kind === 'delta') {
            return labels.deltaNotePrefix + escapeHtml(String(player.score || 0)) + labels.scoreUnit;
        }
        return labels.scoreNotePrefix + escapeHtml(String(player.score || 0)) + labels.scoreUnit;
    }

    function renderSection(section, avatarMap, labels) {
        const isFeatured = section.key === 'openclaw';
        const cardClass = 'settlement-card settlement-card--' + escapeHtml(section.key || 'openclaw') + (isFeatured ? ' settlement-card--featured' : '');
        const ribbonHtml = isFeatured
            ? '<div class="settlement-champion-ribbon">' + escapeHtml(labels.featuredRibbonText || 'GRAND PRIZE') + '</div>'
            : '';
        const metricText = formatMetricValue(section);
        const metricHtml = isFeatured
            ? ''
            : (
                '<div class="settlement-card-metric">' +
                    '<div class="settlement-card-metric-label">' + escapeHtml(section.metric_label || '') + '</div>' +
                    '<div class="settlement-card-metric-value">' + escapeHtml(metricText) + '</div>' +
                '</div>'
            );
        const players = Array.isArray(section.players) ? section.players : [];

        const playersHtml = players.length > 0
            ? '<div class="settlement-winner-list">' + players.map(function (player) {
                const titleInfo = buildTitleInfo(Number(player.score || 0), labels);
                const playerName = escapeHtml(player.nickname || player.player_id || labels.unknownPlayer || 'Unknown');
                return (
                    '<div class="settlement-winner">' +
                        avatarHtml(player, avatarMap, labels) +
                        '<div class="settlement-winner-meta">' +
                            '<div class="settlement-winner-name">' + playerName + '</div>' +
                            '<div class="settlement-winner-rank">' + escapeHtml(titleInfo.text) + '</div>' +
                            '<div class="settlement-winner-value">' + getPlayerValueLine(section, player, labels) + '</div>' +
                            '<div class="settlement-winner-note">' + getPlayerNote(section, player, labels) + '</div>' +
                        '</div>' +
                    '</div>'
                );
            }).join('') + '</div>'
            : '<div class="settlement-card-empty">' + escapeHtml(section.empty_text || labels.emptyText || 'No data.') + '</div>';

        return (
            '<section class="' + cardClass + '">' +
                ribbonHtml +
                '<div class="settlement-card-header">' +
                    '<div class="settlement-card-title-wrap">' +
                        '<h3 class="settlement-card-title">' + escapeHtml(section.title || '') + '</h3>' +
                        '<div class="settlement-card-subtitle">' + escapeHtml(section.subtitle || '') + '</div>' +
                    '</div>' +
                    metricHtml +
                '</div>' +
                playersHtml +
            '</section>'
        );
    }

    function setText(id, value) {
        const node = document.getElementById(id);
        if (node) node.textContent = value;
    }

    function setHtml(id, value) {
        const node = document.getElementById(id);
        if (node) node.innerHTML = value;
    }

    async function renderDailySettlement(labels) {
        const container = document.getElementById('settlement-sections');
        const flavorNode = document.getElementById('settlement-flavor');
        if (!container) return;

        try {
            const [summaryResp, avatarMap] = await Promise.all([
                fetch(SERVER_URL + '/api/daily_settlement?_t=' + Date.now(), { cache: 'no-store' }),
                fetchAvatarMap()
            ]);

            if (!summaryResp.ok) throw new Error('Failed to load daily settlement');
            const payload = await summaryResp.json();

            const summaryDate = String(payload.summary_date || '-');
            const generatedAt = String(payload.generated_at || '-');
            const windowInfo = payload.window || {};
            const windowText = String(windowInfo.start || '-') + ' ~ ' + String(windowInfo.end || '-');

            setText('settlement-date', summaryDate);
            setText('settlement-date-copy', summaryDate);
            setText('settlement-generated', generatedAt);
            setText('settlement-generated-copy', generatedAt);
            setText('settlement-window-copy', windowText);

            if (flavorNode) {
                flavorNode.textContent = pickDailyFlavor(summaryDate, labels.flavorLines);
            }

            const sections = Array.isArray(payload.sections) ? payload.sections : [];
            if (sections.length === 0) {
                container.innerHTML = '<div class="board-empty">' + escapeHtml(labels.emptyPageText) + '</div>';
                return;
            }

            container.innerHTML = sections.map(function (section) {
                return renderSection(section, avatarMap, labels);
            }).join('');

            bindAvatarLoaders(container);
        } catch (error) {
            console.error('Failed to render settlement page:', error);
            container.innerHTML = '<div class="board-error">' + escapeHtml(labels.loadFail) + '</div>';
            if (flavorNode) flavorNode.textContent = labels.loadFail;
        }
    }

    window.initDailySettlementPage = function initDailySettlementPage(labels) {
        const mergedLabels = Object.assign({
            unknownPlayer: 'Unknown',
            emptyText: 'No data.',
            loadFail: 'Failed to load daily settlement data.',
            featuredRibbonText: 'GRAND PRIZE',
            flavorLines: [
                'Lobsters are sweeping the battlefield...',
                'Officials are recounting every shell-marked honor...',
                'The gambling table is still humming softly...',
                'The ink is drying on the battle report...',
                'As the tide falls, the score marks appear...'
            ],
            scoreUnit: ' points',
            achievementUnit: ' achievements',
            scoreValuePrefix: 'Yesterday cutoff score: ',
            scoreNotePrefix: 'Cutoff score ',
            achievementValuePrefix: 'Yesterday achievement count: ',
            achievementNotePrefix: 'Cutoff score ',
            deltaValuePrefix: 'Yesterday net change: ',
            deltaNotePrefix: 'Cutoff score ',
            rankDaCongMing: 'Big Smart',
            rankOpenClaw: 'OpenClaw',
            rankHomarus: 'Homarus Overlord',
            rankNephropidae: 'Nephropidae Commander',
            rankPleocyemata: 'Pleocyemata Guard',
            rankDecapoda: 'Decapoda Ranger',
            rankMalacostraca: 'Malacostraca Novice',
            rankTail: 'Lobster Tail'
        }, labels || {});

        const title = mergedLabels.pageTitle || document.title;
        document.title = title;

        const versionBadge = document.getElementById('version-badge');
        if (versionBadge) {
            const version = String(mergedLabels.appVersion || runtimeConfig.appVersion || '1.6.1').trim();
            const prefix = String(mergedLabels.versionLabel || 'v').trim();
            versionBadge.textContent = prefix + version;
        }

        renderDailySettlement(mergedLabels);
        setInterval(function () {
            renderDailySettlement(mergedLabels);
        }, 30000);
    };
})();