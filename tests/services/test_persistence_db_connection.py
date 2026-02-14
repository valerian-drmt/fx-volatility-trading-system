import psycopg2

from db_config import db_host, db_name, db_password, db_port, db_user


def test_rds_connection():
    connection = psycopg2.connect(
        host=db_host,
        port=db_port,
        user=db_user,
        password=db_password,
        database=db_name,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            result = cursor.fetchone()
        assert result == (1,)
    finally:
        connection.close()