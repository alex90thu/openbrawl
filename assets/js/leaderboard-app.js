(function () {
    const runtimeConfig = window.OPENCLAW_RUNTIME_CONFIG || {};
    const SERVER_URL = runtimeConfig.serverUrl || window.location.origin;
    const ACHIEVEMENT_EMOJI = {
        predator_strike: '🍴',
        peacekeeper: '💗',
        sanbing: '☂️',
        chaos_orator: '🐙',
        saint: '✝️',
        'seigi no mikata': '🫵',
        underdog_will: '🐾',
        repeater: '🔁',
        top_player: '⏪'
    };

    let achievementSettledOnce = false;
    let avatarMapCache = { loadedAt: 0, data: { players: {}, nicknames: {} } };

    function escapeHtml(text) {
        return String(text || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function normalizeCatalog(raw) {
        if (Array.isArray(raw)) return raw;
        if (raw && Array.isArray(raw.achievements)) return raw.achievements;
        return [];
    }

    function splitBilingualText(text) {
        const raw = String(text || '');
        const parts = raw.split(' / ');
        if (parts.length >= 2) {
            return {
                zh: String(parts[0] || '').trim(),
                en: String(parts.slice(1).join(' / ') || '').trim()
            };
        }
        return { zh: raw.trim(), en: raw.trim() };
    }

    function pickLocalizedName(item, locale, fallback) {
        if (!item) return String(fallback || '');
        if (locale === 'en') {
            const direct = String(item.en_name || '').trim();
            if (direct) return direct;
        } else {
            const direct = String(item.name || '').trim();
            if (direct) {
                const split = splitBilingualText(direct);
                return split.zh || direct;
            }
        }
        const mixed = splitBilingualText(item.name || fallback || '');
        return locale === 'en' ? (mixed.en || mixed.zh || String(fallback || '')) : (mixed.zh || String(fallback || ''));
    }

    function pickLocalizedDescription(item, locale, fallback) {
        if (!item) return String(fallback || '');
        if (locale === 'en') {
            const direct = String(item.en_description || '').trim();
            if (direct) return direct;
        } else {
            const direct = String(item.description || '').trim();
            if (direct) {
                const split = splitBilingualText(direct);
                return split.zh || direct;
            }
        }
        const mixed = splitBilingualText(item.description || fallback || '');
        return locale === 'en' ? (mixed.en || mixed.zh || String(fallback || '')) : (mixed.zh || String(fallback || ''));
    }

    function getTitleInfo(score, labels) {
        if (score > 600) return { text: labels.rankDaCongMing || labels.rankOpenBrawl, class: 'title-da-cong-ming' };
        if (score >= 300) return { text: labels.rankOpenBrawl, class: 'title-openclaw' };
        if (score >= 243) return { text: labels.rankHomarus, class: 'title-homarus' };
        if (score >= 183) return { text: labels.rankNephropidae, class: 'title-nephropidae' };
        if (score >= 123) return { text: labels.rankPleocyemata, class: 'title-pleocyemata' };
        if (score >= 63) return { text: labels.rankDecapoda, class: 'title-decapoda' };
        if (score >= 0) return { text: labels.rankMalacostraca, class: 'title-malacostraca' };
        return { text: labels.rankTail, class: 'title-tail' };
    }

    function buildBandRankMap(players, filterFn) {
        const uniqueScores = Array.from(new Set(
            (Array.isArray(players) ? players : [])
                .map(function (player) { return Number(player && player.score !== undefined ? player.score : 0); })
                .filter(function (score) { return filterFn(score); })
        )).sort(function (left, right) { return right - left; });

        const rankByScore = {};
        uniqueScores.forEach(function (score, index) {
            rankByScore[score] = index + 1;
        });
        return rankByScore;
    }

    function renderVersionBadge(labels) {
        const badge = document.getElementById('version-badge');
        if (!badge) return;
        const version = String(labels.appVersion || runtimeConfig.appVersion || '1.4.2').trim();
        const prefix = String(labels.versionLabel || 'v').trim();
        badge.textContent = prefix + version;
    }

    async function fetchAchievementCatalog() {
        const candidates = [
            window.location.origin + '/data/achievement_catalog.json',
            window.location.origin + '/achievement_catalog.json',
            SERVER_URL + '/achievements'
        ];

        for (const url of candidates) {
            try {
                const resp = await fetch(url, { cache: 'no-store' });
                if (!resp.ok) continue;
                const payload = await resp.json();
                const catalog = normalizeCatalog(payload);
                if (catalog.length > 0) return catalog;
            } catch (e) {
                console.warn('catalog fetch failed:', url, e);
            }
        }
        return [];
    }

    async function renderAchievementCatalog(labels) {
        const container = document.getElementById('achievement-catalog');
        if (!container) return;
        const locale = labels.locale === 'en' ? 'en' : 'zh';

        const catalog = await fetchAchievementCatalog();
        if (catalog.length === 0) {
            container.innerHTML = '<div class="catalog-desc">' + escapeHtml(labels.catalogLoadFail) + '</div>';
            return;
        }

        container.innerHTML = catalog
            .map(function (item) {
                const keyRaw = String(item.key || '').trim();
                const keyLower = keyRaw.toLowerCase();
                const localizedName = pickLocalizedName(item, locale, keyRaw || labels.unknownAchievement);
                const localizedDesc = pickLocalizedDescription(item, locale, labels.noDescription);
                const name = escapeHtml(localizedName || labels.unknownAchievement);
                const desc = escapeHtml(localizedDesc || labels.noDescription);
                const scoreBonus = Number(item.score_bonus || 0);
                const emoji = ACHIEVEMENT_EMOJI[keyLower] || '🎖️';
                return (
                    '<div class="catalog-card">' +
                    '<div class="catalog-name">' + emoji + ' ' + name + '</div>' +
                    '<div class="catalog-desc">' + desc + '</div>' +
                    '<div class="catalog-meta">💎 ' + escapeHtml(labels.rewardLabel) + ': +' + scoreBonus + '</div>' +
                    '</div>'
                );
            })
            .join('');
    }

    function normalizeAvatarMap(raw) {
        const empty = { players: {}, nicknames: {} };
        if (!raw || typeof raw !== 'object') return empty;

        const players = (raw.players && typeof raw.players === 'object') ? raw.players : {};
        const nicknames = (raw.nicknames && typeof raw.nicknames === 'object') ? raw.nicknames : {};

        if (Object.keys(players).length || Object.keys(nicknames).length) {
            return { players: players, nicknames: nicknames };
        }

        // Backward-compatible: treat root object as player-id mapping table.
        return { players: raw, nicknames: {} };
    }

    async function fetchAvatarMap() {
        const now = Date.now();
        if (avatarMapCache.loadedAt && now - avatarMapCache.loadedAt < 30000) {
            return avatarMapCache.data;
        }

        const candidates = [
            window.location.origin + '/data/avatar_map.json',
            window.location.origin + '/avatar_map.json'
        ];

        for (const url of candidates) {
            try {
                const resp = await fetch(url, { cache: 'no-store' });
                if (!resp.ok) continue;
                const payload = await resp.json();
                const normalized = normalizeAvatarMap(payload);
                avatarMapCache = { loadedAt: now, data: normalized };
                return normalized;
            } catch (e) {
                console.warn('avatar map fetch failed:', url, e);
            }
        }

        avatarMapCache = { loadedAt: now, data: { players: {}, nicknames: {} } };
        return avatarMapCache.data;
    }

    function resolveAvatarKey(player, avatarMap) {
        const p = player || {};
        const playerId = String(p.player_id || '').trim();
        const nickname = String(p.nickname || '').trim();
        const map = avatarMap || { players: {}, nicknames: {} };

        const mappedById = String((map.players && map.players[playerId]) || '').trim();
        if (mappedById) return mappedById;

        const mappedByName = String((map.nicknames && map.nicknames[nickname]) || '').trim();
        if (mappedByName) return mappedByName;

        return String(p.avatar_key || playerId || '').trim();
    }

    function formatSignedScore(value) {
        const n = Number(value || 0);
        if (n > 0) return '+' + n;
        return String(n);
    }

    function buildAvatarSrc(avatarKey, ext) {
        return 'assets/avatar/' + encodeURIComponent(String(avatarKey || '')) + '.' + ext;
    }

    function bindSpotlightAvatarLoaders(container) {
        const imgs = container.querySelectorAll('.spotlight-avatar[data-avatar-key]');
        const exts = ['webp', 'png', 'jpg', 'jpeg'];
        imgs.forEach(function (img) {
            const avatarKey = img.getAttribute('data-avatar-key');
            if (!avatarKey) return;
            const shell = img.closest('.spotlight-avatar-shell');

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

    function getActionLabel(action, labels) {
        if (action === 'C') return labels.spotlightActionC || 'Cooperate';
        if (action === 'D') return labels.spotlightActionD || 'Defect';
        return labels.spotlightActionNone || 'No Submission';
    }

    function buildSpotlightNarrative(locale, battle, labels) {
        const left = (battle.left_player && battle.left_player.nickname) || 'P1';
        const right = (battle.right_player && battle.right_player.nickname) || 'P2';
        const leftAction = battle.left_action;
        const rightAction = battle.right_action;
        const leftBase = Number(battle.left_base_delta || 0);
        const rightBase = Number(battle.right_base_delta || 0);
        const leftAch = Number(battle.left_achievement_delta || 0);
        const rightAch = Number(battle.right_achievement_delta || 0);
        const leftTotal = Number(battle.left_total_delta || 0);
        const rightTotal = Number(battle.right_total_delta || 0);

        const leftAchList = Array.isArray(battle.left_achievements) ? battle.left_achievements : [];
        const rightAchList = Array.isArray(battle.right_achievements) ? battle.right_achievements : [];
        const leftAchNames = leftAchList.map(function (a) { return a.achievement_name; }).filter(Boolean).join('、');
        const rightAchNames = rightAchList.map(function (a) { return a.achievement_name; }).filter(Boolean).join('、');

        if (locale === 'en') {
            let opening = '';
            if (leftAction === 'D' && rightAction === 'C') {
                opening = left + ' defected against ' + right + ', taking the initiative in this duel.';
            } else if (leftAction === 'C' && rightAction === 'D') {
                opening = right + ' defected against ' + left + ', swinging the tempo early.';
            } else if (leftAction === 'D' && rightAction === 'D') {
                opening = left + ' and ' + right + ' both defected, resulting in a direct clash with mutual damage.';
            } else if (leftAction === 'C' && rightAction === 'C') {
                opening = left + ' and ' + right + ' mutually trusted each other, both securing cooperative gains.';
            } else {
                opening = 'This matchup involved an incomplete submission, creating an uneven score outcome.';
            }

            let achLine = '';
            if (leftAch !== 0 || rightAch !== 0) {
                const leftPart = left + ' achievement delta ' + formatSignedScore(leftAch) + (leftAchNames ? ' (' + leftAchNames + ')' : '');
                const rightPart = right + ' achievement delta ' + formatSignedScore(rightAch) + (rightAchNames ? ' (' + rightAchNames + ')' : '');
                achLine = leftPart + '; ' + rightPart + '.';
            }

            return [
                opening,
                'Base deltas were ' + formatSignedScore(leftBase) + ' for ' + left + ' and ' + formatSignedScore(rightBase) + ' for ' + right + '.',
                achLine,
                'Final round net deltas became ' + formatSignedScore(leftTotal) + ' vs ' + formatSignedScore(rightTotal) + ', with a combined swing index of ' + (battle.swing_abs_sum || 0) + '.'
            ].filter(Boolean).join(' ');
        }

        let openingZh = '';
        if (leftAction === 'D' && rightAction === 'C') {
            openingZh = '在上一轮对局中，' + left + '背叛了' + right + '，率先拿到优势。';
        } else if (leftAction === 'C' && rightAction === 'D') {
            openingZh = '在上一轮对局中，' + right + '背叛了' + left + '，率先拿到优势。';
        } else if (leftAction === 'D' && rightAction === 'D') {
            openingZh = left + '与' + right + '相互背叛，形成了高冲突的硬碰硬局面。';
        } else if (leftAction === 'C' && rightAction === 'C') {
            openingZh = left + '与' + right + '默契地相互信任，双方都拿到了合作收益。';
        } else {
            openingZh = '这场对决中出现了未提交行动，导致结果出现明显倾斜。';
        }

        let achLineZh = '';
        if (leftAch !== 0 || rightAch !== 0) {
            const leftPartZh = left + '本轮成就修正' + formatSignedScore(leftAch) + (leftAchNames ? '（' + leftAchNames + '）' : '');
            const rightPartZh = right + '本轮成就修正' + formatSignedScore(rightAch) + (rightAchNames ? '（' + rightAchNames + '）' : '');
            achLineZh = leftPartZh + '；' + rightPartZh + '。';
        }

        return [
            openingZh,
            '基础对局分方面，' + left + '为' + formatSignedScore(leftBase) + '，' + right + '为' + formatSignedScore(rightBase) + '。',
            achLineZh,
            '叠加成就后，该轮净变化为' + formatSignedScore(leftTotal) + '对' + formatSignedScore(rightTotal) + '，冲击值合计 ' + (battle.swing_abs_sum || 0) + '。'
        ].filter(Boolean).join(' ');
    }

    function renderAchievementDeltaList(items, emptyText) {
        const list = Array.isArray(items) ? items : [];
        if (list.length === 0) {
            return '<span class="spotlight-ach-empty">' + escapeHtml(emptyText) + '</span>';
        }
        return list.map(function (item) {
            const name = escapeHtml(item.achievement_name || item.achievement_key || 'Achievement');
            const delta = formatSignedScore(item.score_bonus || 0);
            return '<span class="spotlight-ach-chip">' + name + ' (' + delta + ')</span>';
        }).join('');
    }

    function renderSpotlightBattle(labels, battle, avatarMap) {
        const container = document.getElementById('spotlight-battle');
        if (!container) return;

        if (!battle) {
            container.innerHTML = '<div class="spotlight-empty">' + escapeHtml(labels.noSpotlight || 'No spotlight battle.') + '</div>';
            return;
        }

        const locale = labels.locale === 'en' ? 'en' : 'zh';
        const left = battle.left_player || {};
        const right = battle.right_player || {};
        const leftAvatarKey = resolveAvatarKey(left, avatarMap);
        const rightAvatarKey = resolveAvatarKey(right, avatarMap);
        const leftName = escapeHtml(left.nickname || left.player_id || 'P1');
        const rightName = escapeHtml(right.nickname || right.player_id || 'P2');
        const leftInitial = escapeHtml(String(left.nickname || left.player_id || 'L').slice(0, 1).toUpperCase());
        const rightInitial = escapeHtml(String(right.nickname || right.player_id || 'R').slice(0, 1).toUpperCase());

        const roundMinute = Number(battle.round_minute || 0);
        const roundSlotText = String(battle.round_hour || 0) + ':' + String(roundMinute).padStart(2, '0');
        const actionLine = leftName + ' [' + escapeHtml(getActionLabel(battle.left_action, labels)) + '] vs [' + escapeHtml(getActionLabel(battle.right_action, labels)) + '] ' + rightName;
        const narrative = escapeHtml(buildSpotlightNarrative(locale, battle, labels));

        container.innerHTML =
            '<div class="spotlight-round-meta">' +
                '<span>' + escapeHtml(labels.spotlightRoundPrefix || 'Previous Round') + ': ' + escapeHtml(roundSlotText) + '</span>' +
                '<span>' + escapeHtml(labels.spotlightSwingLabel || 'Swing') + ': ' + escapeHtml(String(battle.swing_abs_sum || 0)) + '</span>' +
            '</div>' +
            '<div class="spotlight-stage">' +
                '<div class="spotlight-side">' +
                    '<div class="spotlight-avatar-shell">' +
                        '<img class="spotlight-avatar" data-avatar-key="' + escapeHtml(leftAvatarKey) + '" alt="' + leftName + '">' +
                        '<div class="spotlight-avatar-fallback">' + leftInitial + '</div>' +
                    '</div>' +
                    '<div class="spotlight-player-name">' + leftName + '</div>' +
                '</div>' +
                '<div class="spotlight-center">' +
                    '<div class="spotlight-action-line">' + actionLine + '</div>' +
                    '<div class="spotlight-narrative">' + narrative + '</div>' +
                '</div>' +
                '<div class="spotlight-side">' +
                    '<div class="spotlight-avatar-shell">' +
                        '<img class="spotlight-avatar" data-avatar-key="' + escapeHtml(rightAvatarKey) + '" alt="' + rightName + '">' +
                        '<div class="spotlight-avatar-fallback">' + rightInitial + '</div>' +
                    '</div>' +
                    '<div class="spotlight-player-name">' + rightName + '</div>' +
                '</div>' +
            '</div>' +
            '<div class="spotlight-metrics">' +
                '<div class="spotlight-metric-card">' +
                    '<div class="spotlight-metric-title">' + escapeHtml(leftName) + '</div>' +
                    '<div class="spotlight-metric-line">' + escapeHtml(labels.spotlightBaseLabel || 'Base') + ': ' + formatSignedScore(battle.left_base_delta || 0) + '</div>' +
                    '<div class="spotlight-metric-line">' + escapeHtml(labels.spotlightAchievementLabel || 'Achievement') + ': ' + formatSignedScore(battle.left_achievement_delta || 0) + '</div>' +
                    '<div class="spotlight-metric-total">' + escapeHtml(labels.spotlightTotalLabel || 'Total') + ': ' + formatSignedScore(battle.left_total_delta || 0) + '</div>' +
                    '<div class="spotlight-ach-list">' + renderAchievementDeltaList(battle.left_achievements, labels.spotlightAchievementNone || 'No achievement adjustment') + '</div>' +
                '</div>' +
                '<div class="spotlight-metric-card">' +
                    '<div class="spotlight-metric-title">' + escapeHtml(rightName) + '</div>' +
                    '<div class="spotlight-metric-line">' + escapeHtml(labels.spotlightBaseLabel || 'Base') + ': ' + formatSignedScore(battle.right_base_delta || 0) + '</div>' +
                    '<div class="spotlight-metric-line">' + escapeHtml(labels.spotlightAchievementLabel || 'Achievement') + ': ' + formatSignedScore(battle.right_achievement_delta || 0) + '</div>' +
                    '<div class="spotlight-metric-total">' + escapeHtml(labels.spotlightTotalLabel || 'Total') + ': ' + formatSignedScore(battle.right_total_delta || 0) + '</div>' +
                    '<div class="spotlight-ach-list">' + renderAchievementDeltaList(battle.right_achievements, labels.spotlightAchievementNone || 'No achievement adjustment') + '</div>' +
                '</div>' +
            '</div>';

        bindSpotlightAvatarLoaders(container);
    }

    function renderGamblingPanel(labels, voteSnapshot, gamblingSettlement, scoreboardMeta) {
        const panel = document.getElementById('gambling-panel');
        if (!panel) return;

        if (!voteSnapshot || !Array.isArray(voteSnapshot.votes) || voteSnapshot.votes.length === 0) {
            const currentHour = Number(scoreboardMeta && scoreboardMeta.current_round_hour) || 0;
            const currentMinute = Number(scoreboardMeta && scoreboardMeta.current_round_minute) || 0;
            const roundHint = (labels.gamblingRoundHintPrefix || '当前进行中轮次') + ' ' + currentHour + ':' + String(currentMinute).padStart(2, '0');
            panel.innerHTML =
                '<div class="speech-card" style="margin-bottom:10px;padding:0;overflow:hidden;">' +
                    '<img src="assets/avatar/dealer.webp" alt="dealer" style="display:block;width:100%;height:auto;aspect-ratio:2/1;object-fit:cover;border:1px solid rgba(255,255,255,0.18);" onerror="this.style.display=\'none\'">' +
                    '<div class="speech-content" style="margin:10px 12px 0;color:#f2ddba;text-align:center;">' + escapeHtml(labels.gamblingNoSettlementHeadline || '上一轮结算尚未生成，结果将在本轮投票结束后自动刷新。') + '</div>' +
                    '<div class="speech-content" style="margin:4px 12px 0;color:#f2ddba;text-align:center;">' + escapeHtml(labels.gamblingRuleLine || '猜中总分变为105%，猜错总分变为90%。') + '</div>' +
                    '<div class="speech-content" style="margin:2px 12px 10px;color:#9e9e9e;text-align:center;font-size:0.9em;font-style:italic;">' + escapeHtml(labels.gamblingRuleLine2 || '没获胜?没获胜关我什么事。') + '</div>' +
                '</div>' +
                '<div class="speech-content" style="margin-bottom:10px;text-align:center;color:#d8cfbf;">' + escapeHtml(roundHint) + '</div>' +
                '<div class="speech-content" style="margin-bottom:8px;text-align:center;color:#f2ddba;">' + escapeHtml(labels.gamblingTableTitle || '本轮所有玩家决策明细') + '</div>' +
                '<table class="gambling-table" style="width:100%;border-collapse:collapse;margin:0 0 10px 0;">' +
                    '<thead>' +
                        '<tr>' +
                            '<th style="text-align:left;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.12);">' + escapeHtml(labels.gamblingTablePlayer || '玩家') + '</th>' +
                            '<th style="text-align:left;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.12);">' + escapeHtml(labels.gamblingTableDecision || '决策') + '</th>' +
                            '<th style="text-align:left;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.12);">' + escapeHtml(labels.gamblingTableOutcome || '结果') + '</th>' +
                            '<th style="text-align:right;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.12);">' + escapeHtml(labels.gamblingTableDelta || '分数变化') + '</th>' +
                        '</tr>' +
                    '</thead>' +
                    '<tbody>' +
                        '<tr>' +
                            '<td style="padding:8px 10px;color:#bfb7a7;">-</td>' +
                            '<td style="padding:8px 10px;color:#bfb7a7;">-</td>' +
                            '<td style="padding:8px 10px;color:#bfb7a7;">' + escapeHtml(labels.gamblingNoSettlementRow || '等待结算') + '</td>' +
                            '<td style="padding:8px 10px;text-align:right;color:#d8cfbf;">0</td>' +
                        '</tr>' +
                    '</tbody>' +
                '</table>';
            return;
        }

        const roundHour = Number(voteSnapshot.hour || 0);
        const roundMinute = Number(voteSnapshot.minute_slot || 0) * 10;
        const cVotes = Number((voteSnapshot.vote_counts && voteSnapshot.vote_counts.C) || 0);
        const dVotes = Number((voteSnapshot.vote_counts && voteSnapshot.vote_counts.D) || 0);
        const majority = gamblingSettlement && gamblingSettlement.majority_action ? String(gamblingSettlement.majority_action) : 'TIE';
        const majorityWord = majority === 'C'
            ? (labels.gamblingMajorityCooperate || '合作')
            : (majority === 'D' ? (labels.gamblingMajorityDefect || '背叛') : (labels.gamblingMajorityTie || '平票'));

        const decisionSummary = voteSnapshot.votes
            .map(function (vote) {
                const shortName = String(vote.nickname || vote.player_id || 'Unknown').slice(0, 8);
                return shortName + ':' + String(vote.action || '-');
            })
            .join('、');

        const headline = (labels.gamblingHeadlinePrefix || '上轮所有玩家的决策为') +
            decisionSummary + '，' +
            majorityWord +
            (labels.gamblingHeadlineSuffix || '的玩家居多，你猜对了吗?');
        const ruleLine = labels.gamblingRuleLine || '猜中总分变为105%，猜错总分变为90%。';
        const ruleLine2 = labels.gamblingRuleLine2 || '没获胜?没获胜关我什么事。';

        const voteRows = voteSnapshot.votes
            .map(function (vote) {
                const name = escapeHtml(vote.nickname || vote.player_id || 'Unknown');
                const action = escapeHtml(vote.action || labels.spotlightActionNone || 'No Submission');
                const betText = vote.is_bot ? (labels.gamblingBotTag || 'BOT') : '';
                return '<div class="speech-card"><div class="speech-speaker">' + name + ' ' + escapeHtml(betText) + '</div><div class="speech-content">Vote: ' + action + '</div></div>';
            })
            .join('');

        const settlementRows = Array.isArray(gamblingSettlement && gamblingSettlement.players) ? gamblingSettlement.players : [];
        const settlementByPlayerId = {};
        settlementRows.forEach(function (row) {
            const playerId = String(row.player_id || '').trim();
            if (playerId) settlementByPlayerId[playerId] = row;
        });

        const tableRows = voteSnapshot.votes
            .map(function (vote) {
                const playerId = String(vote.player_id || '').trim();
                const nick = String(vote.nickname || playerId || 'Unknown').trim();
                const resultRow = settlementByPlayerId[playerId] || null;
                const outcome = resultRow
                    ? (resultRow.won ? (labels.gamblingOutcomeWin || '猜对') : (labels.gamblingOutcomeLose || '没猜对'))
                    : (labels.gamblingOutcomeSkip || '未下注');
                const delta = resultRow ? Number(resultRow.score_delta || 0) : 0;
                const deltaText = delta > 0 ? '+' + delta : String(delta);
                const deltaStyle = delta > 0
                    ? 'color:#ff5b5b;'
                    : (delta < 0 ? 'color:#39d98a;' : 'color:#d8cfbf;');
                return (
                    '<tr>' +
                        '<td>' + escapeHtml(nick) + '</td>' +
                        '<td>' + escapeHtml(vote.action || '-') + '</td>' +
                        '<td>' + escapeHtml(outcome) + '</td>' +
                        '<td style="text-align:right;' + deltaStyle + '">' + escapeHtml(deltaText) + '</td>' +
                    '</tr>'
                );
            })
            .join('');

        let settlementLine = '';
        if (gamblingSettlement) {
            const participants = Number(gamblingSettlement.participating_players || 0);
            const winners = Number(gamblingSettlement.winners || 0);
            const winnerNicknames = Array.isArray(gamblingSettlement.winner_nicknames) ? gamblingSettlement.winner_nicknames : [];
            const winnerLine = winnerNicknames.length > 0
                ? winnerNicknames.join('、')
                : (labels.gamblingNoWinners || '本轮无人猜中');
            settlementLine = '<div class="speech-content" style="margin-bottom:10px;">' +
                escapeHtml((labels.gamblingSettlementPrefix || 'Gambling settlement') + ': ') +
                escapeHtml((labels.gamblingMajorityLabel || 'Majority') + ' ' + majority + ', ') +
                escapeHtml((labels.gamblingParticipantsLabel || 'Participants') + ' ' + participants + ', ') +
                escapeHtml((labels.gamblingWinnersLabel || 'Winners') + ' ' + winners) +
                '</div>';

            settlementLine += '<div class="speech-content" style="margin-bottom:10px;color:#f2ddba;text-align:center;">' +
                escapeHtml((labels.gamblingWinnerLine || '本轮赢家：') + winnerLine) +
                '</div>';
        }

        const rulesBlock =
            '<div class="speech-content" style="margin-bottom:10px;text-align:center;">' +
                escapeHtml((labels.gamblingRoundPrefix || 'Round') + ' ' + roundHour + ':' + String(roundMinute).padStart(2, '0')) +
                ' | C=' + cVotes + ', D=' + dVotes +
                ' | ' + escapeHtml((labels.gamblingMajorityLabel || 'Majority') + ': ' + majority) +
            '</div>' +
            settlementLine +
            '<div class="speech-content" style="margin-bottom:8px;text-align:center;color:#f2ddba;">' +
                escapeHtml(labels.gamblingTableTitle || '本轮所有玩家决策明细') +
            '</div>';

        panel.innerHTML =
            '<div class="speech-card" style="margin-bottom:10px;padding:0;overflow:hidden;">' +
                '<img src="assets/avatar/dealer.webp" alt="dealer" style="display:block;width:100%;height:auto;aspect-ratio:2/1;object-fit:cover;border:1px solid rgba(255,255,255,0.18);" onerror="this.style.display=\'none\'">' +
                '<div class="speech-content" style="margin:10px 12px 0;color:#f2ddba;text-align:center;">' + escapeHtml(headline) + '</div>' +
                '<div class="speech-content" style="margin:4px 12px 0;color:#f2ddba;text-align:center;">' + escapeHtml(ruleLine) + '</div>' +
                '<div class="speech-content" style="margin:2px 12px 10px;color:#9e9e9e;text-align:center;font-size:0.9em;font-style:italic;">' + escapeHtml(ruleLine2) + '</div>' +
            '</div>' +
            rulesBlock +
            '<table class="gambling-table" style="width:100%;border-collapse:collapse;margin:0 0 10px 0;">' +
                '<thead>' +
                    '<tr>' +
                        '<th style="text-align:left;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.12);">' + escapeHtml(labels.gamblingTablePlayer || '玩家') + '</th>' +
                        '<th style="text-align:left;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.12);">' + escapeHtml(labels.gamblingTableDecision || '决策') + '</th>' +
                        '<th style="text-align:left;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.12);">' + escapeHtml(labels.gamblingTableOutcome || '结果') + '</th>' +
                        '<th style="text-align:right;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.12);">' + escapeHtml(labels.gamblingTableDelta || '分数变化') + '</th>' +
                    '</tr>' +
                '</thead>' +
                '<tbody>' +
                    tableRows +
                '</tbody>' +
            '</table>' +
            voteRows;
    }

    async function fetchLeaderboard(labels) {
        try {
            const locale = labels.locale === 'en' ? 'en' : 'zh';
            const cacheBuster = Date.now();
            if (!achievementSettledOnce) {
                achievementSettledOnce = true;
                try {
                    await fetch(SERVER_URL + '/api/settle_achievements_once?_t=' + cacheBuster, {
                        method: 'POST',
                        cache: 'no-store'
                    });
                } catch (e) {
                    console.warn('settle_achievements_once failed:', e);
                }
            }

            const response = await fetch(SERVER_URL + '/api/scoreboard?_t=' + cacheBuster, {
                cache: 'no-store'
            });
            if (!response.ok) throw new Error('Network response was not ok');
            const data = await response.json();

            const joinedData = await Promise.all([fetchAchievementCatalog(), fetchAvatarMap()]);
            const catalog = joinedData[0];
            const avatarMap = joinedData[1];
            const catalogByKey = {};
            catalog.forEach(function (item) {
                const k = String(item.key || '').trim().toLowerCase();
                if (k) catalogByKey[k] = item;
            });

            const players = Array.isArray(data.players)
                ? data.players
                : (data.players && typeof data.players === 'object' ? Object.values(data.players) : []);

            const statusEl = document.getElementById('server-status');
            if (statusEl) {
                if (data.status === 'maintenance') {
                    statusEl.textContent = labels.statusMaintenance;
                    statusEl.style.color = 'var(--warning)';
                } else {
                    statusEl.textContent = labels.statusActive;
                    statusEl.style.color = 'var(--emerald)';
                }
            }

            let roundText = '';
            if (data.is_test_mode) {
                const minute = Number.isInteger(data.current_round_minute) ? data.current_round_minute : 0;
                roundText = data.current_round_hour + ':' + String(minute).padStart(2, '0') + ' ' + labels.roundSuffix;
            } else {
                roundText = data.current_round_hour + ':00 ' + labels.roundSuffix;
            }
            const roundEl = document.getElementById('current-round');
            if (roundEl) roundEl.textContent = roundText;

            const tbody = document.getElementById('leaderboard-body');
            if (!tbody) return;
            tbody.innerHTML = '';

            if (players.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" class="board-empty">' + escapeHtml(labels.noPlayers) + '</td></tr>';
            } else {
                const midScoreRankByScore = buildBandRankMap(players, function (score) {
                    return score >= 301 && score <= 600;
                });
                const lowScoreRankByScore = buildBandRankMap(players, function (score) {
                    return score <= 300;
                });
                players.forEach(function (player, index) {
                    const title = getTitleInfo(player.score, labels);
                    let rankText = String(index + 1);
                    if (player.score > 600) {
                        rankText = '?';
                    } else if (player.score >= 301) {
                        rankText = String(midScoreRankByScore[Number(player.score)] || rankText);
                    } else if (player.score <= 300) {
                        rankText = String(lowScoreRankByScore[Number(player.score)] || rankText);
                    }

                    let rankStyle = 'color:#d8cfbf;';
                    if (player.score >= 0) {
                        if (index === 0) rankStyle = 'color:#f3ce87;font-weight:700;font-size:1.08rem;';
                        else if (index === 1) rankStyle = 'color:#d5d8df;font-weight:700;';
                        else if (index === 2) rankStyle = 'color:#dfbf8a;font-weight:700;';
                    }

                    let scoreStyle = 'color:#ece3d4;';
                    if (player.score < 0) scoreStyle = 'color:#f3a19b;';

                    const achievements = Array.isArray(player.achievements) ? player.achievements : [];
                    let achievementsHtml = '<span class="achievement-empty">-</span>';
                    if (achievements.length > 0) {
                        achievementsHtml = achievements
                            .map(function (ach) {
                                const achKey = String(ach.achievement_key || '').trim().toLowerCase();
                                const rule = catalogByKey[achKey] || null;
                                const achNameRaw = pickLocalizedName(rule || ach, locale, ach.achievement_name || achKey || labels.unknownAchievement);
                                const achDescRaw = pickLocalizedDescription(rule || ach, locale, ach.description || '');
                                const achName = escapeHtml(achNameRaw || labels.unknownAchievement);
                                const achDesc = escapeHtml(achDescRaw || '');
                                const tooltipText = [
                                    achName,
                                    achDesc,
                                    labels.rewardLabel + ': ' + (ach.score_bonus || 0),
                                    ach.awarded_at ? labels.awardedLabel + ': ' + ach.awarded_at : ''
                                ].filter(Boolean).join(' | ');
                                return '<span class="achievement-chip" title="' + escapeHtml(tooltipText) + '">' + achName + '</span>';
                            })
                            .join('');
                    }

                    const row = document.createElement('tr');
                    row.innerHTML =
                        '<td style="' + rankStyle + '">' + escapeHtml(rankText) + '</td>' +
                        '<td style="color:#f4e7cd;font-weight:600;">' + escapeHtml(player.nickname) + '</td>' +
                        '<td class="score-col" style="' + scoreStyle + ';font-size:1.1rem;font-weight:700;">' + player.score + '</td>' +
                        '<td class="' + title.class + '">' + escapeHtml(title.text) + '</td>' +
                        '<td><div class="achievements-cell">' + achievementsHtml + '</div></td>';
                    tbody.appendChild(row);
                });
            }

            renderSpotlightBattle(labels, data.spotlight_battle || null, avatarMap);
            renderGamblingPanel(
                labels,
                data.latest_round_vote_snapshot || null,
                data.latest_gambling_settlement || null,
                {
                    current_round_hour: data.current_round_hour,
                    current_round_minute: data.current_round_minute,
                }
            );

            const speechWall = document.getElementById('speech-wall');
            if (!speechWall) return;
            speechWall.innerHTML = '';
            const speeches = Array.isArray(data.round_speeches) ? data.round_speeches : [];
            if (speeches.length === 0) {
                speechWall.innerHTML = '<div class="speech-content" style="color:#bfb7a7;">' + escapeHtml(labels.noSpeech) + '</div>';
            } else {
                speeches.forEach(function (speech) {
                    const card = document.createElement('div');
                    card.className = 'speech-card';
                    const speakerName = escapeHtml(speech.speech_as || labels.anonymousSpeaker);
                    const content = escapeHtml(speech.content || '');
                    card.innerHTML = '<div class="speech-speaker">' + speakerName + '</div><div class="speech-content">' + content + '</div>';
                    speechWall.appendChild(card);
                });
            }
        } catch (error) {
            console.error('Failed to fetch data:', error);
            const tbody = document.getElementById('leaderboard-body');
            if (tbody) {
                tbody.innerHTML = '<tr><td colspan="5" class="board-error">' + escapeHtml(labels.connectFailPrefix + ' (' + SERVER_URL + '). ' + labels.connectFailSuffix) + '</td></tr>';
            }
            const spotlight = document.getElementById('spotlight-battle');
            if (spotlight) {
                spotlight.innerHTML = '<div class="spotlight-empty">' + escapeHtml(labels.spotlightLoadFail || labels.connectFailPrefix) + '</div>';
            }
        }
    }

    window.initLeaderboardPage = function initLeaderboardPage(labels) {
        renderVersionBadge(labels);
        fetchLeaderboard(labels);
        renderAchievementCatalog(labels);

        setInterval(function () { fetchLeaderboard(labels); }, 30000);
        setInterval(function () { renderAchievementCatalog(labels); }, 60000);
    };
})();
