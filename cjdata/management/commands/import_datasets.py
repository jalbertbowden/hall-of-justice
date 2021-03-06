from django.core.management.base import BaseCommand
from django.db import transaction
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
import argparse

from csv import DictReader
from itertools import zip_longest
from cjdata.models import (Category, Dataset, STATE_NATL_LOOKUP)
import re

validate_url = URLValidator()


class Command(BaseCommand):
    help = 'Imports datasets from a particularly formatted CSV'

    def add_arguments(self, parser):
        # Positional arguments
        parser.add_argument('filepath', nargs='?', type=argparse.FileType('r'))

        # Named (optional) arguments
        parser.add_argument('-n', '--dry-run',
                            action='store_true',
                            dest='dryrun',
                            default=False,
                            help='Run through file and output errors, but don\'t save datasets')

    def handle(self, *args, **options):
        # Define some helper functions for fixing fields

        def remap_keys(iterable, mapping):
            '''Some fields need manual remapping to Dataset field names'''
            for key, value in iterable:
                if key in mapping and value:
                    key = mapping[key]
                yield key, value

        def fields_to_lists(iterable, fields):
            for key, value in iterable:
                if key in fields:
                    value = [v.strip() for v in value.split(",") if v.strip() != '']
                yield key, value

        def standardize_key(k):
            '''Many fields can be fixed by lowercasing and replacing certain characters'''
            return str(k).lower().strip().replace('? (y/n)', '').replace(' ', '_').replace('/', '_')

        def reformat_keys(iterable, func):
            '''Iterate over keys and run standardize_key'''
            for key, value in iterable:
                new_key = func(key)
                if value:
                    yield new_key, value

        def booleanize(iterable, fields):
            for key, value in iterable:
                if value and key in fields:
                    value = True if value.lower().startswith('y') else False
                yield key, value

        def clean_url(url):
                url = url.strip()
                try:
                    validate_url(url)
                    return url
                except ValidationError:
                    return None

        def clean_states(states_list):
            values_set = (v.strip() for v in states_list)
            values_set = ('US' if v.startswith('Nat') else v for v in values_set)
            values_set = (v for v in values_set if v in STATE_NATL_LOOKUP.keys())
            return list(values_set)

        def make_categories(cat_entry, subcat_entry):
            category_entries = cat_entry.split(',') if cat_entry else []
            cat_entries_cleaned = (c.strip() for c in category_entries)
            subcategory_entries = subcat_entry.split(',') if subcat_entry else []
            subcat_entries_cleaned = (c.strip() for c in subcategory_entries)
            cat_zip = zip_longest(cat_entries_cleaned, subcat_entries_cleaned, fillvalue=None)
            return cat_zip

        def titlecase(s):
            return re.sub(r"[A-Za-z]+('[A-Za-z]+)?",
                          lambda mo: mo.group(0)[0].upper() + mo.group(0)[1:].lower(),
                          s)

        def clean_sectors(sectors_list):
            return list(titlecase(s.strip()) for s in sectors_list)

        fp = options.get('filepath', None)
        save_objects = not options.get('dryrun', False)
        verbosity = options.get('verbosity', None)

        # Actual script exection
        if fp:
            reader = DictReader(fp)
            data_rows = [r for r in reader]
            for row in data_rows:
                # Reformat keys (make lowercase)
                data_iterable = reformat_keys(row.items(), standardize_key)
                # Remap keys to model fields
                mapping = {
                    standardize_key("Private/Government"): "sectors",
                    standardize_key("Is this data updated? (Y/N)"): "updated",
                    # "category": "categories",
                    "internet_availability": "internet_available",
                    "tag": "tags",
                    "state": "states",
                    "format": "formats",
                    "sublocation": "division_names",
                    "location": "resource_location"
                }
                data_iterable = remap_keys(data_iterable, mapping)
                # Make list fields from strings
                list_fields = ("tags", "formats", "sectors", "states", "division_names")
                data_iterable = fields_to_lists(data_iterable, list_fields)
                # Coerce select values to booleans
                boolean_fields = ("mappable", "updated", "population_data", "internet_available")
                data_iterable = booleanize(data_iterable, boolean_fields)
                # Back to a dict, clean states, sectors and url
                item = dict(data_iterable)
                item['states'] = clean_states(item.get('states', []))
                item['sectors'] = clean_sectors(item.get('sectors', []))
                raw_url = item.pop('url', None)
                if raw_url:
                    item['url'] = clean_url(raw_url)

                # Get category paths
                category_raw = item.pop('category', None)
                subcategory_raw = item.pop('subcategory', None)
                category_tuples = make_categories(category_raw, subcategory_raw)

                #  Get title, url, etc.
                item_title = item.get('title', None)
                item_url = item.get('url', None)
                item_group_name = item.get('group_name', None)

                if verbosity > 0:
                    if item_title:
                        self.stdout.write("Title: '{}'".format(item_title))
                    else:
                        self.stderr.write("Title not provided for '{}'".format(item_url))
                if verbosity > 0:
                    if item_group_name:
                        self.stdout.write("Group: '{}'".format(item_group_name))
                    else:
                        self.stderr.write("Group name not provided")
                if item_url and verbosity > 1:
                    self.stdout.write("\tURL: '{}'".format(item.get('url')))
                # Handle categories and subcategories
                categories = []
                for cat_name, subcat_name in category_tuples:
                    cat_repr = "{} -> {}".format(cat_name, subcat_name)
                    if cat_name:
                        try:
                            top_cat = Category.objects.get(name__iexact=cat_name.strip(), parent__isnull=True)
                        except Category.DoesNotExist:
                            self.stderr.write("\tNo top-level category '{}' for {}".format(cat_name, cat_repr))
                            continue
                        if subcat_name:
                            try:
                                cat_obj = Category.objects.get(name__iexact=subcat_name.strip(), parent_id=top_cat.id)
                                categories.append(cat_obj)
                                if verbosity > 1:
                                    self.stdout.write("\tFound '{}' category".format(cat_repr))
                            except Category.MultipleObjectsReturned:
                                self.stderr.write("\tMultiple category matches for {}".format(cat_repr))
                            except Category.DoesNotExist:
                                self.stderr.write("\tNo '{}' category".format(cat_repr))
                        else:
                            categories.append(top_cat)
                    else:
                        self.stderr.write("\t No top-level category name for {}!".format(cat_repr))
                # If we don't have a title and a group_name,we probably shouldn't create an entry.
                if item_title and item_group_name:
                    dataset = None
                    if save_objects:
                        with transaction.atomic():
                            dataset = Dataset.objects.create(**item)
                            for cat_obj in categories:
                                if verbosity > 2:
                                    self.stdout.write("Adding dataset to category '{}'".format(cat_obj.path))
                                dataset.categories.add(cat_obj)
                            try:
                                dataset.full_clean()
                                dataset.save()
                            except ValidationError as e:
                                self.stderr.write("Failed to save dataset '{}':\n\t{}".format(item_title, str(e)))
                                if verbosity > 2:
                                    self.stderr.write("\n".join(["{}: {}".format(k, str(v)) for k, v in item.items()]))
                    else:
                        if verbosity > 0:
                            self.stdout.write("Would create dataset: '{}'\n\n".format(item.get('title', '')))
                    if dataset:
                        if verbosity > 0:
                            self.stdout.write("\tCreated Dataset: {}\n".format(dataset))
                else:
                    self.stderr.write("\tNot enough data to create a dataset! Need at least a title and group_name")
                    if verbosity > 2:
                        item_info = "\n\t".join(["{}: {}".format(k, str(v)) for k, v in item.items()])
                        self.stderr.write(item_info)
        else:
            self.stderr.write("File path not provided. Please provide a path to a CSV file to process")
