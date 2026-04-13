#!/usr/bin/env python3
import sys

def fix_plane_get(filepath):
    print(f"📖 Lendo {filepath}...")
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    start = content.find('def plane_get(path, params=None):')
    if start == -1:
        print("❌ Função plane_get() não encontrada!")
        return False
    
    end = content.find('\ndef ', start + 1)
    if end == -1:
        end = len(content)
    
    old_func = content[start:end]
    
    new_func = '''def plane_get(path, params=None):
    url = f"{PLANE_BASE}/workspaces/{_plane_slug()}/{path}"
    for attempt in range(5):
        try:
            r = requests.get(
                url,
                headers=_headers_plane(),
                params=params or {},
                timeout=30
            )
            
            if r.status_code == 429 and attempt < 4:
                raw = r.headers.get("Retry-After", "45")
                try:
                    wait = int(raw)
                except ValueError:
                    wait = 45
                wait = min(max(wait, 5), 120)
                print(f"   ⏳ Rate limit Plane — aguardando {wait}s...")
                time.sleep(wait)
                continue
            
            r.raise_for_status()
            break
            
        except (requests.exceptions.Timeout, 
                requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError) as e:
            if attempt < 4:
                wait = min(10 * (2 ** attempt), 120)
                print(f"   ⚠️  Plane conexão falhou (tentativa {attempt+1}/5): {type(e).__name__} — aguardando {wait}s...")
                time.sleep(wait)
                continue
            else:
                raise TimeoutError(f"Plane API falhou após {attempt+1} tentativas: {e}")
        
        except requests.exceptions.RequestException as e:
            if attempt < 4:
                wait = min(10 * (2 ** attempt), 120)
                print(f"   ⚠️  Plane erro HTTP (tentativa {attempt+1}/5): {e} — aguardando {wait}s...")
                time.sleep(wait)
                continue
            else:
                raise
    
    data = r.json()
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return data

'''
    
    if old_func in content:
        content = content.replace(old_func, new_func)
        print("✅ Função plane_get() melhorada!")
    else:
        print("❌ Não consegui localizar a função exatamente")
        return False
    
    backup = filepath + '.backup2'
    with open(backup, 'w', encoding='utf-8') as f:
        with open(filepath, 'r', encoding='utf-8') as orig:
            f.write(orig.read())
    print(f"📦 Backup: {backup}")
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    print(f"💾 {filepath} atualizado!")
    
    return True

if __name__ == '__main__':
    filepath = sys.argv[1] if len(sys.argv) > 1 else 'scripts/fetch_metrics.py'
    try:
        success = fix_plane_get(filepath)
        if success:
            print("\n✅ Patch aplicado!")
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"❌ Erro: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
