from haystack.query import SearchQuerySet

sqs = SearchQuerySet().facet('states').facet('group_name').facet('sectors').facet('access_type')
