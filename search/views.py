from haystack.views import FacetedSearchView
from haystack.query import SearchQuerySet
from django.views.generic import View
from django.http import JsonResponse
import collections

basestring = (str, bytes)


class BetterFacetedSearchView(FacetedSearchView):

    def extra_context(self):
        extra = super(BetterFacetedSearchView, self).extra_context()

        if self.form.selected_facets:
            selected_facets = [(k.replace('_exact', ''), v) for k, v in (item.split(':') for item in self.form.selected_facets)]
            extra['selected_facets'] = dict(selected_facets)

        return extra


class AutocompleteView(View):
    """provides autocompletion JSON endpoint"""

    def get(self, request):
        def flatten(l):
            for el in l:
                if isinstance(el, collections.Iterable) and not isinstance(el, basestring):
                    for sub in flatten(el):
                        yield sub
                else:
                    yield el
        query = request.GET.get('q', '').lower()
        sqs = SearchQuerySet().using('autocomplete').filter(content=query)

        # texts = ((t for t in s) if not s.__hash__ else s.text for s in sqs)
        texts = flatten((s.text for s in sqs))
        lower_texts = (t.lower() for t in texts)
        suggestions = ({'value': p.title()} for p in set(lower_texts) if query.lower() in p.lower())

        return JsonResponse({'results': list(suggestions)[:100]})