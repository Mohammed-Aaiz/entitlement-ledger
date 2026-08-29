import re
with open('frontend/src/pages/Dashboard.tsx', 'r', encoding='utf-8') as f:
    t = f.read()
t = t.replace("import ErrorBoundary from '../components/ErrorBoundary';", "")
with open('frontend/src/pages/Dashboard.tsx', 'w', encoding='utf-8') as f:
    f.write(t)

with open('frontend/src/pages/Home.tsx', 'r', encoding='utf-8') as f:
    t = f.read()
t = t.replace("import { Link } from 'react-router-dom';", "")
with open('frontend/src/pages/Home.tsx', 'w', encoding='utf-8') as f:
    f.write(t)
