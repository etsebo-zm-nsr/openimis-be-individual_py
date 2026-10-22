import json

from individual.models import Individual
from individual.tests.test_helpers import IndividualGQLTestCase, create_individual


class NullableDobGQLTest(IndividualGQLTestCase):
    """createIndividual / updateIndividual accept an input without dob."""

    def _run(self, query_str):
        response = self.query(
            query_str,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"}
        )
        return json.loads(response.content)

    def test_create_individual_without_dob(self):
        content = self._run('''
            mutation {
              createIndividual(
                input: {
                  firstName: "NoDob"
                  lastName: "Gql"
                }
              ) {
                clientMutationId
                internalId
              }
            }
        ''')
        self.assertNotIn('errors', content)
        self.assert_mutation_success(content['data']['createIndividual']['internalId'])
        self.assertIsNone(Individual.objects.get(first_name="NoDob", last_name="Gql").dob)

    def test_update_individual_without_dob(self):
        individual = create_individual(self.admin_user.username, {'dob': None})
        content = self._run(f'''
            mutation {{
              updateIndividual(
                input: {{
                  id: "{individual.id}"
                  firstName: "Renamed"
                  lastName: "Gql"
                }}
              ) {{
                clientMutationId
                internalId
              }}
            }}
        ''')
        self.assertNotIn('errors', content)
        self.assert_mutation_success(content['data']['updateIndividual']['internalId'])
        updated = Individual.objects.get(id=individual.id)
        self.assertEqual(updated.first_name, "Renamed")
        self.assertIsNone(updated.dob)
