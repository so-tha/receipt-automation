"""
Database management module.
Handles SQLAlchemy configuration and database initialization.
"""

from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.pool import StaticPool
from src.core.config import config_obj
from pathlib import Path

# Base class for all models
Base = declarative_base()

# Create engine
_engine_options = {
    'echo': config_obj.SQLALCHEMY_ECHO,
}

# Use StaticPool for SQLite to avoid threading issues
if 'sqlite' in config_obj.DATABASE_URL:
    _engine_options['poolclass'] = StaticPool
    _engine_options['connect_args'] = {'check_same_thread': False}

engine = create_engine(config_obj.DATABASE_URL, **_engine_options)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_database():
    """Initialize the database and create all tables."""
    # Ensure data directory exists
    db_path = Path(config_obj.DATABASE_URL.replace('sqlite:///', ''))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Create all tables
    Base.metadata.create_all(bind=engine)
    
    # Run migrations to add missing columns
    _run_migrations()


def _run_migrations():
    """Run database migrations to add missing columns."""
    with engine.connect() as conn:
        inspector = inspect(engine)
        
        # Check if 'reports' table exists
        if 'reports' in inspector.get_table_names():
            columns = {col['name'] for col in inspector.get_columns('reports')}
            
            # Add is_locked column if missing
            if 'is_locked' not in columns:
                conn.execute(text('''
                    ALTER TABLE reports 
                    ADD COLUMN is_locked BOOLEAN DEFAULT 0
                '''))
                conn.commit()
                print("[OK] Adicionada coluna 'is_locked' a tabela 'reports'")
            
            # Add extracted_data column if missing (JSON storage)
            if 'extracted_data' not in columns:
                conn.execute(text('''
                    ALTER TABLE reports 
                    ADD COLUMN extracted_data TEXT
                '''))
                conn.commit()
                print("[OK] Adicionada coluna 'extracted_data' a tabela 'reports'")
            
            # Add file_hash column if missing (SHA256 for deduplication)
            if 'file_hash' not in columns:
                conn.execute(text('''
                    ALTER TABLE reports 
                    ADD COLUMN file_hash VARCHAR(64)
                '''))
                conn.commit()
                print("[OK] Adicionada coluna 'file_hash' a tabela 'reports'")
        
        # Check if 'audit_logs' table exists
        if 'audit_logs' in inspector.get_table_names():
            columns = {col['name'] for col in inspector.get_columns('audit_logs')}
            
            # Ensure details column exists
            if 'details' not in columns:
                conn.execute(text('''
                    ALTER TABLE audit_logs 
                    ADD COLUMN details TEXT
                '''))
                conn.commit()
                print("[OK] Adicionada coluna 'details' a tabela 'audit_logs'")


def get_session() -> Session:
    """Get a new database session."""
    return SessionLocal()


def close_session(session: Session):
    """Close a database session."""
    if session:
        session.close()
