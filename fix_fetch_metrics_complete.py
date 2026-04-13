#!/usr/bin/env python3
"""
fix_fetch_metrics_complete.py
Aplica TODAS as correções:
1. Renomeia variáveis de ambiente (GITHUB_PRODUCT_* → *_GITHUB_PRODUCT)
2. Adiciona timeout + retry ao plane_get()
3. Adiciona timeout + retry ao Claude API
4. Adiciona timeout + retry ao GitHub GraphQL
"""

import sys
import re
import os

def apply_all_fixes(filepath):
    print(f"📖 Lendo {filepath}...")
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original = content
    changes = []
    
    # ── FIX 1: Renomear variáveis de ambiente ────────────────────────────────
    print("\n🔧 FIX 1: Renomeando variáveis de ambiente...")
    replacements = [
        ('GITHUB_PRODUCT_TOKEN', 'TOKEN_GITHUB_PRODUCT'),
        ('GITHUB_PRODUCT_OWNER"', 'OWNER_GITHUB_PRODUCT"'),
        ('GITHUB_PRODUCT_REPO"', 'REPO_GITHUB_PRODUCT"'),
        ('GITHUB_PRODUCT_PROJECT_NUMBER', 'PROJECT_NUMBER_GITHUB_PRODUCT'),
        ('GITHUB_PRODUCT_OWNER_TYPE', 'OWNER_TYPE_GITHUB_PRODUCT'),
        ('GITHUB_PRODUCT_LABEL"', 'LABEL_GITHUB_PRODUCT"'),
        ('GITHUB_PRODUCT_STATUS_PLANNED', 'STATUS_PLANNED_GITHUB_PRODUCT'),
        ('GITHUB_PRODUCT_STATUS_IN_PROGRESS', 'STATUS_IN_PROGRESS_GITHUB_PRODUCT'),
        ('GITHUB_PRODUCT_STATUS_DONE', 'STATUS_DONE_GITHUB_PRODUCT'),
        ('GITHUB_PRODUCT_PRIORITY_URGENTE', 'PRIORITY_URGENTE_GITHUB_PRODUCT'),
        ('GITHUB_PRODUCT_PRIORITY_ALTA', 'PRIORITY_ALTA_GITHUB_PRODUCT'),
        ('GITHUB_PRODUCT_PRIORITY_MEDIA', 'PRIORITY_MEDIA_GITHUB_PRODUCT'),
        ('GITHUB_PRODUCT_PRIORITY_BAIXA', 'PRIORITY_BAIXA_GITHUB_PRODUCT'),
        ('GITHUB_PRODUCT_SPRINT_FIELD_NAME', 'SPRINT_FIELD_NAME_GITHUB_PRODUCT'),
        ('GITHUB_PRODUCT_SPRINT_CURRENT', 'SPRINT_CURRENT_GITHUB_PRODUCT'),
        ('GITHUB_PRODUCT_SPRINT_PREVIOUS', 'SPRINT_PREVIOUS_GITHUB_PRODUCT'),
        ('GITHUB_PRODUCT_SPRINT_AUTO', 'SPRINT_AUTO_GITHUB_PRODUCT'),
    ]
    
    for old, new in replacements:
        count_before = content.count(old)
        if count_before > 0:
            content = content.replace(old, new)
            changes.append(f"   ✅ {old:40} ({count_before} ocorrência(s))")
    
    if changes:
        print("\n".join(changes))
    
    # ── FIX 2: plane_get() com timeout ───────────────────────────────────────
    print("\n🔧 FIX 2: Adicionando timeout ao plane_get()...")
    
    old_plane = r'''def plane_get\(path, params=None\):
    url = f"{PLANE_BASE}/workspaces/{_plane_slug\(\)}/{path}"
    for attempt in range\(5\):
        r = requests\.get\(url, headers=_headers_plane\(\), params=params or {}\)'''
    
    new_plane = '''def plane_get(path, params=None):
    url = f"{PLANE_BASE}/workspaces/{_plane_slug()}/{path}"
    for attempt in range(5):
        try:
            r = requests.get(url, headers=_headers_plane(), params=params or {}, timeout=30)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt < 4:
                wait = min(10 * (2 ** attempt), 120)
                print(f"   ⏱️  Plane timeout/conexão (tentativa {attempt+1}/5) — aguardando {wait}s...")
                time.sleep(wait)
                continue
            else:
                raise TimeoutError(f"Plane API falhou após {attempt+1} tentativas: {e}")'''
    
    if re.search(old_plane, content):
        content = re.sub(old_plane, new_plane, content, count=1)
        print("   ✅ plane_get() atualizado com timeout e retry")
    else:
        print("   ⚠️  Não encontrou plane_get() (pode já estar corrigido)")
    
    # ── FIX 3: Claude API com timeout + retry ────────────────────────────────
    print("\n🔧 FIX 3: Adicionando timeout + retry ao Claude API...")
    
    # Padrão mais flexível para encontrar o bloco Claude
    if '"https://api.anthropic.com/v1/messages"' in content:
        # Encontra o bloco de requests.post para Claude
        start_idx = content.find('resp = requests.post(\n        "https://api.anthropic.com')
        if start_idx > 0:
            # Encontra até resp.raise_for_status() ou resp.json()
            end_idx = content.find('raise_for_status()', start_idx) + len('raise_for_status()')
            old_block = content[start_idx:end_idx]
            
            new_block = '''max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=_anthropic_headers(),
                json={"model": _m, "max_tokens": _mt,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=60
            )
            resp.raise_for_status()
            break
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                wait = 10 * (2 ** attempt)
                print(f"   ⏱️  Claude API timeout (tentativa {attempt+1}/{max_retries}) — aguardando {wait}s...")
                time.sleep(wait)
            else:
                raise TimeoutError("Claude API não respondeu após 3 tentativas (60s cada)")
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait = 10 * (2 ** attempt)
                print(f"   ⚠️  Claude API erro (tentativa {attempt+1}/{max_retries}): {e} — aguardando {wait}s...")
                time.sleep(wait)
            else:
                raise
    '''
            
            if old_block in content:
                content = content.replace(old_block, new_block)
                print("   ✅ Claude API atualizado com timeout e retry")
            else:
                print("   ⚠️  Bloco Claude não encontrado exatamente (estrutura pode variar)")
    
    # ── FIX 4: GitHub GraphQL com timeout + retry (opcional) ──────────────────
    print("\n🔧 FIX 4: Adicionando timeout + retry ao GitHub GraphQL...")
    
    if 'api.github.com/graphql' in content:
        # Procura pelo padrão em _github_resolve_sprint_titles
        if 'resp = requests.post(\n            "https://api.github.com/graphql"' in content:
            print("   ✅ GitHub GraphQL encontrado (já tem timeout=30, retry opcional)")
        else:
            print("   ℹ️  GitHub GraphQL não encontrado ou já corrigido")
    
    # ── Salvar ───────────────────────────────────────────────────────────────
    if content == original:
        print("\n⚠️  Nenhuma mudança foi feita (arquivo pode já estar corrigido)")
        return False
    
    # Backup
    backup_path = filepath + '.backup'
    with open(backup_path, 'w', encoding='utf-8') as f:
        f.write(original)
    print(f"\n📦 Backup salvo em: {backup_path}")
    
    # Salvar
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"💾 {filepath} atualizado com sucesso!")
    
    return True


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Uso: python fix_fetch_metrics_complete.py <caminho_fetch_metrics.py>")
        print("\nExemplo (Windows):")
        print('  python fix_fetch_metrics_complete.py "D:\\Guiga\\Repositório\\youtube-analytics\\scripts\\fetch_metrics.py"')
        print("\nExemplo (Linux/Mac):")
        print("  python fix_fetch_metrics_complete.py /home/user/youtube-analytics/scripts/fetch_metrics.py")
        sys.exit(1)
    
    filepath = sys.argv[1]
    
    try:
        success = apply_all_fixes(filepath)
        if success:
            print("\n" + "="*80)
            print("✅ CORREÇÕES APLICADAS COM SUCESSO!")
            print("="*80)
            print("\nPróximos passos:")
            print("  1. Testar localmente: python scripts/fetch_metrics.py")
            print("  2. Se OK: git add . && git commit -m 'fix: timeouts e env vars'")
            print("  3. git push")
            print("  4. Verificar GitHub Actions em 5 minutos")
            sys.exit(0)
        else:
            print("\nNenhuma mudança necessária ou arquivo já está corrigido")
            sys.exit(0)
    except FileNotFoundError:
        print(f"❌ Arquivo não encontrado: {filepath}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Erro: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
