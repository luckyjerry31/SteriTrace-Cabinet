# SteriTrace Cabinet

Application Streamlit originale pour la traçabilité de stérilisation en cabinet dentaire.

Elle couvre :

- tableau de bord,
- création d'un cycle de stérilisation en 4 étapes,
- libération de charge par opérateur,
- composition de lots,
- DLU automatique selon le conditionnement,
- étiquettes thermiques avec QR code,
- recherche/contrôle de lot,
- fiche de traçabilité patient imprimable,
- stockage Supabase ou SQLite local de démonstration.

## Installation locale

```bash
pip install -r requirements.txt
streamlit run app.py
```

Sans Supabase, l'application démarre en SQLite local de démonstration.

## Déploiement Streamlit Cloud

1. Mettez `app.py`, `requirements.txt` et `supabase_schema.sql` dans votre dépôt GitHub.
2. Créez un projet Supabase.
3. Exécutez `supabase_schema.sql` dans Supabase SQL Editor.
4. Dans Streamlit Cloud, ajoutez les secrets :

```toml
SUPABASE_URL="https://xxxx.supabase.co"
SUPABASE_KEY="votre_cle_supabase"
```

5. Lancez l'application.

## Points à personnaliser

Dans `app.py`, modifiez :

- `DEFAULT_OPERATORS`,
- `DEFAULT_AUTOCLAVES`,
- `DEFAULT_DEVICES`,
- `PACKAGING_RULES`,
- les programmes de stérilisation dans `CYCLE_TYPES`.

## Important

Cette application aide à organiser la traçabilité. Elle ne remplace pas une procédure qualité, une validation réglementaire, une qualification d'autoclave, ni un avis de votre référent ou organisme compétent. Pour un usage réel, verrouillez l'accès, les politiques Supabase RLS et la gestion RGPD.
