import os
from typing import List


def generate_round_robin_html(players: List[str], out_path: str = "templates/round_robin.html", points=(3,1,0)):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    n = len(players)

    # Build HTML
    title = f"Круговой турнир — {n} игроков"
    players_js_array = "[" + ",".join(f'\"{p}\"' for p in players) + "]"

    html = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: Arial, Helvetica, sans-serif; padding:16px; color:#222 }}
    .wrap {{ max-width:1000px; margin:0 auto }}
    h1 {{ font-size:20px }}
    table {{ border-collapse:collapse; width:100%; margin-bottom:16px }}
    th, td {{ border:1px solid #ddd; padding:6px; text-align:center }}
    th {{ background:#f5f5f5 }}
    .player-col {{ text-align:left; padding-left:10px }}
    input.score {{ width:48px }}
    .standings th {{ background:#eee }}
    @media (max-width:640px) {{ .wrap {{ padding:8px }} input.score {{ width:40px }} }}
  </style>
</head>
<body>
<div class="wrap">
  <h1>{title}</h1>

  <h2>Раунд-Матчи</h2>
  <p>Введите очки каждого матча — таблица автоматически пересчитает очки (3/1/0).</p>

  <table id="matches">
    <thead>
      <tr>
        <th></th>
"""

    # header row
    for p in players:
        html += f"        <th class=\"player-col\">{p}</th>\n"

    html += "      </tr>\n    </thead>\n    <tbody>\n"

    # body rows: for each player show cells vs other players
    for i, p in enumerate(players):
        html += f"      <tr>\n        <th class=\"player-col\">{p}</th>\n"
        for j, q in enumerate(players):
            if i == j:
                html += "        <td style=\"background:#f9f9f9\">—</td>\n"
            elif i < j:
                cell_id = f"m_{i}_{j}"
                html += (
                    f"        <td>"
                    f"<input class=\"score\" id=\"{cell_id}_a\" data-i=\"{i}\" data-j=\"{j}\" type=\"number\" min=0 value=\"\">"
                    f" — "
                    f"<input class=\"score\" id=\"{cell_id}_b\" data-i=\"{i}\" data-j=\"{j}\" type=\"number\" min=0 value=\"\">"
                    f"</td>\n"
                )
            else:
                # mirror cell: show result placeholder referencing same ids
                html += "        <td class=\"muted\">(см. выше)</td>\n"
        html += "      </tr>\n"

    html += "    </tbody>\n  </table>\n"

    # Standings table
    html += "\n  <h2>Турнирная таблица</h2>\n  <table id=\"standings\" class=\"standings\">\n    <thead>\n      <tr>\n        <th>#</th>\n        <th>Участник</th>\n        <th>И</th>\n        <th>В</th>\n        <th>Н</th>\n        <th>П</th>\n        <th>Очки</th>\n      </tr>\n    </thead>\n    <tbody>\n"

    for idx, p in enumerate(players, start=1):
        html += (
            f"      <tr data-player=\"{idx-1}\">\n        <td class=\"pos\">{idx}</td>\n        <td class=\"player-col\">{p}</td>\n        <td class=\"played\">0</td>\n        <td class=\"wins\">0</td>\n        <td class=\"draws\">0</td>\n        <td class=\"losses\">0</td>\n        <td class=\"points\">0</td>\n      </tr>\n"
        )

    html += "    </tbody>\n  </table>\n"

    # JS: compute standings from input values
    html += """

  <script>
    const players = {PLAYERS_JS};
    const pts_win = {PTS0};
    const pts_draw = {PTS1};
    const pts_loss = {PTS2};

    function recompute() {
      const n = players.length;
      const stats = Array.from({length: n}, () => ({played:0, wins:0, draws:0, losses:0, points:0}));

      for (let i=0;i<n;i++){
        for (let j=i+1;j<n;j++){
          const a = document.getElementById(`m_${i}_${j}_a`).value;
          const b = document.getElementById(`m_${i}_${j}_b`).value;
          if (a === '' || b === '') continue;
          const sa = parseInt(a,10);
          const sb = parseInt(b,10);
          stats[i].played += 1;
          stats[j].played += 1;
          if (sa > sb) {
            stats[i].wins +=1; stats[i].points += pts_win;
            stats[j].losses +=1; stats[j].points += pts_loss;
          } else if (sa < sb) {
            stats[j].wins +=1; stats[j].points += pts_win;
            stats[i].losses +=1; stats[i].points += pts_loss;
          } else {
            stats[i].draws +=1; stats[i].points += pts_draw;
            stats[j].draws +=1; stats[j].points += pts_draw;
          }
        }
      }

      // write to table
      const tbody = document.querySelector('#standings tbody');
      // build array for sorting
      const arr = stats.map((s, idx) => ({idx: idx, name: players[idx], played: s.played, wins: s.wins, draws: s.draws, losses: s.losses, points: s.points}));
      arr.sort((a,b) => b.points - a.points || (b.wins - a.wins));

      // update rows
      arr.forEach((r, pos) => {
        const row = tbody.querySelector(`tr[data-player="${r.idx}"]`);
        row.querySelector('.pos').textContent = pos+1;
        row.querySelector('.played').textContent = r.played;
        row.querySelector('.wins').textContent = r.wins;
        row.querySelector('.draws').textContent = r.draws;
        row.querySelector('.losses').textContent = r.losses;
        row.querySelector('.points').textContent = r.points;
      });
    }

    // attach listeners
    document.querySelectorAll('input.score').forEach(inp => {
      inp.addEventListener('input', recompute);
    });

    // initial compute
    recompute();
  </script>

</div>
</body>
</html>
"""

    # substitute placeholders (safe for JS braces)
    html = html.replace('{TITLE}', title).replace('{PLAYERS_JS}', players_js_array).replace('{PTS0}', str(points[0])).replace('{PTS1}', str(points[1])).replace('{PTS2}', str(points[2]))

    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)

    return out_path


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Generate round-robin HTML table')
    parser.add_argument('names', nargs='*', help='Player names (if omitted, creates 4 sample players)')
    parser.add_argument('--out', '-o', default='templates/round_robin.html')
    args = parser.parse_args()
    if not args.names:
        players = [f'Игрок {i}' for i in range(1,5)]
    else:
        players = args.names
    path = generate_round_robin_html(players, out_path=args.out)
    print('Wrote', path)
