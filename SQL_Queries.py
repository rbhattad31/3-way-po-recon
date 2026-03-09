import mysql.connector

def get_grn_details(po_number):

    try:
        connection = mysql.connector.connect(
            host="",
            user="",
            password="",
            database=""
        )

        cursor = connection.cursor(dictionary=True)

        query = """
        SELECT grn_number, received_quantity
        FROM grn
        WHERE po_number = %s
        """

        cursor.execute(query, (po_number,))

        result = cursor.fetchone()

        cursor.close()
        connection.close()

        if result:
            return result
            # return result["grn_number"], result["received_quantity"]
        else:
            return {}
            # return "", ""

    except Exception as e:

        print("Database Error:", e)

        return {}